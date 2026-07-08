"""Shared Gemini Batch API + DynamoDB helper for batch orchestrators."""

import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path

from google import genai
from google.genai import types

from transcription_reviewer.config import config as global_config
from transcription_reviewer.infrastructure.dynamodb_client import DynamoDBClient
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.utils.batch_jsonl import BatchEntry

logger = logging.getLogger(__name__)


class GeminiBatchService:
    """Encapsulates Gemini client, caching, JSONL build/upload/submit,
    result fetch/parse, and DynamoDB table operations for batch orchestrators."""

    def __init__(
        self,
        dynamodb_client: DynamoDBClient,
        s3_client: S3Client,
    ):
        self._dynamodb = dynamodb_client
        self._s3 = s3_client
        self._model_name = global_config.gemini_model
        self._temperature = global_config.temperature
        self._max_tokens = global_config.max_tokens
        self._thinking_budget = global_config.thinking_budget
        self._split_by_words_max = global_config.split_by_words_max
        self._max_word_diff = global_config.max_word_diff
        self._max_retries = global_config.max_retries
        self._cache_enabled = global_config.gemini_cache_enabled
        self._cache_ttl_seconds = global_config.gemini_cache_ttl_seconds
        self._cache_guard_seconds = global_config.gemini_cache_guard_seconds
        self._webhook_url = global_config.gemini_batch_webhook_url
        self._temporary_fix_bucket = global_config.temporary_fix_bucket
        self._dead_letter_bucket = global_config.dead_letter_bucket
        self._fix_tracker_table = global_config.fix_tracker_table
        self._batch_jobs_table = global_config.batch_jobs_table

        self._client = genai.Client(api_key=global_config.google_api_key)

    # ---- Gemini API ----

    @staticmethod
    def compute_prompt_hash(system_prompt: str) -> str:
        normalized = "\n".join(line.strip() for line in system_prompt.strip().splitlines())
        return hashlib.md5(normalized.encode()).hexdigest()[:8]

    def get_or_create_cache(
        self,
        system_prompt: str,
        caches: dict[str, tuple[str, float]],
    ) -> str | None:
        """Get or create a cached content entry for the system prompt.
        Mutates `caches` dict (keyed by prompt_hash) with (cache_name, expiry).
        Returns cache name or None if caching disabled/failed."""
        if not self._cache_enabled:
            return None

        prompt_hash = self.compute_prompt_hash(system_prompt)
        display_name = f"transcription_fix_{prompt_hash}"

        if prompt_hash in caches:
            cached_name, expiry = caches[prompt_hash]
            if time.time() < (expiry - self._cache_guard_seconds):
                logger.debug("Reusing in-memory cache: %s", cached_name)
                return cached_name
            logger.info("Cache expired: %s", cached_name)

        try:
            for existing in self._client.caches.list():
                if existing.display_name == display_name:
                    expiry_ts = existing.expire_time.timestamp()
                    if time.time() < (expiry_ts - self._cache_guard_seconds):
                        caches[prompt_hash] = (existing.name, expiry_ts)
                        logger.info("Reusing existing Gemini cache: %s", existing.name)
                        return existing.name
        except Exception as e:
            logger.warning("Failed to list caches: %s", e)

        try:
            cache_response = self._client.caches.create(
                model=self._model_name,
                config=types.CreateCachedContentConfig(
                    display_name=display_name,
                    system_instruction=system_prompt,
                    ttl=f"{self._cache_ttl_seconds}s",
                ),
            )
            cached_name = cache_response.name
            expiry = time.time() + self._cache_ttl_seconds
            caches[prompt_hash] = (cached_name, expiry)
            logger.info("Cache created: %s", cached_name)
            return cached_name
        except Exception as e:
            logger.warning("Failed to create cache: %s, falling back to uncached", e)
            return None

    def _ensure_webhook_enabled(self) -> None:
        """Check if the static webhook is disabled and re-enable it if needed."""
        if not self._webhook_url:
            return

        try:
            result = self._client.webhooks.list()
            webhooks = list(result.webhooks) if result.webhooks else []
            for wh in webhooks:
                if wh.uri == self._webhook_url:
                    logger.info("Found webhook for URL %s: id=%s, state=%s", self._webhook_url, wh.id, wh.state)
                    if wh.state and "disabled" in wh.state.lower():
                        logger.warning(
                            "Webhook %s is disabled (%s), re-enabling...", wh.id, wh.state
                        )
                        self._client.webhooks.update(id=wh.id, uri=wh.uri, state="enabled")
                        logger.info("Webhook %s re-enabled", wh.id)
                    return
            logger.warning("No webhook found for URI %s — it may be stale or deleted", self._webhook_url)
        except Exception as e:
            logger.warning("Failed to check/enable webhook: %s", e)

    def build_and_upload_jsonl(
        self,
        entries: list[BatchEntry],
        caches: dict[str, tuple[str, float]],
        reasoning_media_ids: set[str] | None = None,
    ) -> str:
        """Build JSONL from entries and upload to GCS.
        Returns gcs_input_file URI."""
        jsonl_content = self._build_jsonl(entries, caches, reasoning_media_ids)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(jsonl_content)
            temp_path = f.name

        try:
            file_size = Path(temp_path).stat().st_size
            logger.info("Uploading JSONL to GCS: %s (size: %d bytes)", temp_path, file_size)
            upload_result = self._client.files.upload(
                file=temp_path,
                config=types.UploadFileConfig(
                    display_name=f"input_{int(time.time())}",
                    mime_type="jsonl",
                ),
            )
            gcs_input_file = upload_result.name
            logger.info("Uploaded JSONL to GCS: %s", gcs_input_file)
            return gcs_input_file
        except Exception as e:
            logger.exception("Failed to upload JSONL to GCS: %s", e)
            raise
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def submit_batch(self, gcs_input_file: str) -> str:
        """Submit a batch job to Gemini using a previously uploaded GCS file.
        Returns batch_job_name. Cleans up GCS file on failure."""
        try:
            # Ensure static webhook is enabled before submitting batch
            self._ensure_webhook_enabled()

            # Note: Static webhooks are used (created via create_webhook.py)
            # They fire for all batch jobs automatically - no per-job config needed
            # Note: dest parameter is not supported in Developer API mode (only Enterprise).
            # Gemini auto-generates output filenames >40 chars which can't be deleted via API,
            # but they expire automatically after their retention period.
            batch_config = types.CreateBatchJobConfig(
                display_name=f"batch_{int(time.time())}",
            )
            batch = self._client.batches.create(
                model=self._model_name,
                src=gcs_input_file,
                config=batch_config,
            )
        except Exception as e:
            logger.exception("Failed to submit batch job, cleaning up GCS file: %s", e)
            self.delete_gcs_file(gcs_input_file)
            raise
        logger.info("Submitted batch job: %s", batch.name)
        return batch.name

    def fetch_batch_results(self, output_file_uri: str) -> list[dict]:
        """Download output JSONL from GCS and parse into result dicts."""
        try:
            raw = self._client.files.download(file=output_file_uri)
            if isinstance(raw, bytes):
                content = raw.decode("utf-8")
            elif hasattr(raw, "read"):
                content = raw.read().decode("utf-8")
            else:
                content = str(raw)
        except Exception:
            logger.exception("Failed to download output file: %s", output_file_uri)
            raise

        results = []
        for line in content.strip().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                results.append({
                    "key": record.get("key", ""),
                    "response": record.get("response"),
                    "error": record.get("error"),
                })
            except json.JSONDecodeError:
                logger.warning("Failed to parse result line: %s", line[:200])
        return results

    def delete_gcs_file(self, gcs_file: str) -> None:
        try:
            self._client.files.delete(name=gcs_file)
            logger.info("Deleted GCS file: %s", gcs_file)
        except Exception as e:
            logger.warning("Failed to delete GCS file %s: %s", gcs_file, e)

    def delete_prompt_caches(self, caches: dict[str, tuple[str, float]]) -> None:
        """Delete all cached content. Called only at finalization."""
        for cache_name, _ in caches.values():
            try:
                self._client.caches.delete(name=cache_name)
                logger.info("Deleted prompt cache: %s", cache_name)
            except Exception as e:
                logger.warning("Failed to delete cache %s: %s", cache_name, e)

    # ---- JSONL ----

    def _build_jsonl(
        self,
        entries: list[BatchEntry],
        caches: dict[str, tuple[str, float]],
        reasoning_media_ids: set[str] | None = None,
    ) -> str:
        """Build Gemini batch JSONL string from entries."""
        lines = []
        reasoning_media_ids = reasoning_media_ids or set()

        for entry in entries:
            is_reasoning = entry.media_id in reasoning_media_ids
            cache_entry = caches.get(entry.prompt_hash) if not is_reasoning else None
            cache_name = cache_entry[0] if cache_entry else None

            request: dict = {
                "contents": [{"role": "user", "parts": [{"text": entry.content}]}],
                "generation_config": {
                    "temperature": self._temperature,
                    "top_p": 0.1,
                    "top_k": 1,
                    "max_output_tokens": self._max_tokens,
                },
            }

            if is_reasoning:
                request["generation_config"]["thinking_config"] = {
                    "thinking_budget": self._thinking_budget,
                }

            if cache_name:
                request["cached_content"] = cache_name
            else:
                request["system_instruction"] = {
                    "parts": [{"text": entry.system_prompt}]
                }

            record = {"key": entry.media_id, "request": request}
            lines.append(json.dumps(record, ensure_ascii=False))

        return "\n".join(lines) + "\n"


    # ---- FIX_TRACKER_TABLE ----

    def register_fix_tracker_entry(
        self,
        media_id: str,
        stem: str,
        original_word_count: int,
        total_chunks: int,
    ) -> None:
        """Register a new entry in the fix tracker table for batch processing."""
        self._dynamodb.put_item(
            table_name=self._fix_tracker_table,
            item={
                "media_id": {"S": media_id},
                "stem": {"S": stem},
                "retry_number": {"N": "1"},
                "completed": {"BOOL": False},
                "diff_value": {"N": "999999"},
                "original_word_count": {"N": str(original_word_count)},
                "total_chunks": {"N": str(total_chunks)},
            },
        )

    def get_fix_tracker(self, media_id: str) -> dict | None:
        return self._dynamodb.get_item(
            table_name=self._fix_tracker_table,
            key={"media_id": {"S": media_id}},
        )

    def update_fix_tracker(
        self,
        media_id: str,
        completed: bool | None = None,
        diff_value: int | None = None,
        retry_number: int | None = None,
    ) -> None:
        parts = []
        values = {}
        if completed is not None:
            parts.append("completed = :c")
            values[":c"] = {"BOOL": completed}
        if diff_value is not None:
            parts.append("diff_value = :d")
            values[":d"] = {"N": str(diff_value)}
        if retry_number is not None:
            parts.append("retry_number = :r")
            values[":r"] = {"N": str(retry_number)}

        if not parts:
            return

        self._dynamodb.update_item(
            table_name=self._fix_tracker_table,
            key={"media_id": {"S": media_id}},
            update_expression="SET " + ", ".join(parts),
            expression_values=values,
        )

    def delete_fix_tracker(self, media_id: str) -> None:
        self._dynamodb.delete_item(
            table_name=self._fix_tracker_table,
            key={"media_id": {"S": media_id}},
        )

    def collect_completed_stems(self) -> dict[str, list[str]]:
        """Scan tracker for all completed entries, grouped by stem.
        Returns {stem: [media_id, ...]}."""
        items = self._dynamodb.scan(
            table_name=self._fix_tracker_table,
            filter_expression="completed = :c",
            expression_values={":c": {"BOOL": True}},
        )
        result: dict[str, list[str]] = {}
        for item in items:
            stem = item.get("stem", {}).get("S", "")
            media_id = item.get("media_id", {}).get("S", "")
            if stem and media_id:
                result.setdefault(stem, []).append(media_id)
        return result

    def delete_fix_tracker_by_stem(self, stem: str) -> None:
        """Delete all tracker entries whose media_id starts with stem."""
        items = self._dynamodb.scan(
            table_name=self._fix_tracker_table,
            filter_expression="begins_with(media_id, :s)",
            expression_values={":s": {"S": stem}},
        )
        for item in items:
            media_id = item.get("media_id", {}).get("S", "")
            if media_id:
                self._dynamodb.delete_item(
                    table_name=self._fix_tracker_table,
                    key={"media_id": {"S": media_id}},
                )

    # ---- BATCH_JOBS_TABLE ----

    def store_batch_job(
        self,
        batch_job_id: str,
        gcs_input_file: str,
        caches: dict[str, tuple[str, float]],
    ) -> None:
        caches_map = {
            prompt_hash: {
                "M": {
                    "name": {"S": name},
                    "expiry": {"N": str(expiry)},
                }
            }
            for prompt_hash, (name, expiry) in caches.items()
        }
        self._dynamodb.put_item(
            table_name=self._batch_jobs_table,
            item={
                "batch_job_id": {"S": batch_job_id},
                "gcs_input_file": {"S": gcs_input_file},
                "output_file_uri": {"NULL": True},
                "status": {"S": "submitted"},
                "prompt_caches": {"M": caches_map},
            },
        )

    def get_batch_job(self, batch_job_id: str) -> dict | None:
        """Returns job_record or None if not found."""
        return self._dynamodb.get_item(
            table_name=self._batch_jobs_table,
            key={"batch_job_id": {"S": batch_job_id}},
        )

    def extract_caches(self, batch_job: dict) -> dict[str, tuple[str, float]]:
        """Extract deserialized caches dict from a batch_job record."""
        caches: dict[str, tuple[str, float]] = {}
        caches_raw = batch_job.get("prompt_caches", {}).get("M", {})
        for prompt_hash, entry in caches_raw.items():
            inner = entry.get("M", {})
            name = inner.get("name", {}).get("S", "")
            expiry = float(inner.get("expiry", {}).get("N", "0"))
            if name:
                caches[prompt_hash] = (name, expiry)
        return caches

    def delete_gemini_batch(self, batch_job_id: str) -> None:
        """Delete batch job from Gemini API."""
        try:
            self._client.batches.delete(name=batch_job_id)
            logger.info("Deleted Gemini batch job: %s", batch_job_id)
        except Exception:
            logger.warning("Failed to delete Gemini batch job: %s (may already be deleted)", batch_job_id)

    def delete_batch_job_record(self, batch_job_id: str) -> None:
        """Delete batch job record from DynamoDB."""
        try:
            self._dynamodb.delete_item(
                table_name=self._batch_jobs_table,
                key={"batch_job_id": {"S": batch_job_id}},
            )
            logger.info("Deleted batch job record: %s", batch_job_id)
        except Exception:
            logger.warning("Failed to delete batch job record: %s", batch_job_id)

    # ---- Dead Letter ----

    def dead_letter_media(self, stem: str, transcription_bucket: str) -> None:
        """Copy original media + temp fix chunks to dead-letter bucket, clean up."""
        # Copy original media files
        objects = self._s3.list_objects(transcription_bucket, prefix=f"{stem}.")
        for obj in objects:
            key = obj["Key"]
            self._s3.copy_object(transcription_bucket, key, self._dead_letter_bucket, key)
        if objects:
            logger.info(
                "Copied %d source files for %s to dead-letter bucket", len(objects), stem
            )

        # Copy any fixed chunks from temp bucket
        temp_objects = self._s3.list_objects(self._temporary_fix_bucket, prefix=f"{stem}")
        for obj in temp_objects:
            key = obj["Key"]
            self._s3.copy_object(
                self._temporary_fix_bucket, key, self._dead_letter_bucket, f"temp/{key}"
            )

        # Delete source files from transcription bucket
        self._s3.delete_objects_by_prefix(transcription_bucket, f"{stem}.")

        # Clean up temp files
        self._s3.delete_objects_by_prefix(self._temporary_fix_bucket, f"{stem}")

        # Clean up tracker entries
        self.delete_fix_tracker_by_stem(stem)

        logger.info("Dead-lettered %s: cleaned up source, temp files and tracker entries", stem)

    # ---- Properties ----

    @property
    def max_word_diff(self) -> int:
        return self._max_word_diff

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def temporary_fix_bucket(self) -> str:
        return self._temporary_fix_bucket

    @property
    def split_by_words_max(self) -> int:
        return self._split_by_words_max
