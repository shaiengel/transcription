"""Gemini pipeline implementation with system prompt caching."""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from transcription_reviewer.config import config as global_config

from google import genai
from google.genai import types

from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.infrastructure.sqs_client import SQSClient
from transcription_reviewer.models.schemas import ReviewResult, TranscriptionFile
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.services.fix_tracker import FixTrackerEntry, FixTrackerService
from transcription_reviewer.utils.batch_jsonl import BatchEntry

logger = logging.getLogger(__name__)



class GeminiPipeline(LLMPipeline):
    """Gemini API pipeline with per-file system prompts and caching."""

    def __init__(
        self,
        s3_client: S3Client,
        sqs_client: SQSClient,
        api_key: str,
        sqs_queue_url: str,
        temporary_fix_bucket: str,
        model_name: str = "gemini-2.5-flash",
        temperature: float = 0.1,
        max_tokens: int = 60000,
        split_by_words_max: int = 5000,
        max_word_diff: int = 100,
        thinking_budget: int = 1024,
        fix_tracker: FixTrackerService | None = None,
    ):
        self._s3_client = s3_client
        self._sqs_client = sqs_client
        self._sqs_queue_url = sqs_queue_url
        self._temporary_fix_bucket = temporary_fix_bucket
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._split_by_words_max = split_by_words_max
        self._max_word_diff = max_word_diff
        self._thinking_budget = thinking_budget
        self._fix_tracker = fix_tracker

        self._client = genai.Client(api_key=api_key)

        # Captured once at instantiation — used as lambda_started_at for all files this invocation
        self._invocation_started_at: str = self._now_iso()

        # Set per-invocation by invoke(); used by _is_running_out_of_time()
        self._context = None

        # Set by prepare_data(); consumed by invoke()
        self._tracker_entry: FixTrackerEntry | None = None

        # Cache management: maps system_prompt -> (cached_content_name, expiry_time)
        self._prompt_caches: dict[str, tuple[str, float]] = {}

    def prepare_data(self, files: list[TranscriptionFile], context=None) -> list[BatchEntry]:
        """Claim/resume tracker entry then prepare batch entries with token counting.

        Returns an empty list if the file should be skipped (active lambda, dead-lettered).
        """
        self._tracker_entry = None

        if self._fix_tracker and files:
            f = files[0]
            time_remaining_ms = context.get_remaining_time_in_millis() if context else 600_000
            tracker_entry, should_skip = self._resolve_tracker_entry(
                media_id=f.stem,
                transcription_bucket=f.transcription_bucket,
                time_remaining_ms=time_remaining_ms,
            )
            if should_skip:
                return []
            self._tracker_entry = tracker_entry

        entries: list[BatchEntry] = []

        for f in files:
            total_tokens = self._count_tokens(f.content)
            word_count = len(f.content.split())
            logger.info(f"File {f.stem}: {word_count} words, {total_tokens} tokens")

            entries.append(
                BatchEntry(
                    record_id=f.stem,
                    system_prompt=f.system_prompt,
                    content=f.content,
                    token_count=total_tokens,
                    transcription_bucket=f.transcription_bucket,
                    output_bucket=f.output_bucket,
                    context_files_bucket=f.context_files_bucket,
                )
            )

        logger.info(f"Prepared {len(entries)} batch entries for Gemini")
        return entries

    def invoke(
        self,
        prepared_data: list[BatchEntry],
        context=None,
        **kwargs,
    ) -> list[tuple[str, str, bool]]:
        """Call Gemini for each entry using cached system prompts."""
        self._context = context
        results = []

        for entry in prepared_data:
            try:
                cache_name = self._get_or_create_cache(entry.system_prompt)
                config = self._build_config(cache_name, entry.system_prompt)
                record_id, fixed_text, success = self._invoke_entry(entry, config, self._tracker_entry)
                results.append((record_id, fixed_text, success))
            except TimeoutError:
                raise
            except Exception as e:
                logger.error(f"Failed to process {entry.record_id}: {e}")
                results.append((entry.record_id, "", False))

        return results

    def _get_time_remaining_ms(self) -> int:
        if self._context is None:
            return 600_000
        return self._context.get_remaining_time_in_millis()

    def _is_running_out_of_time(self) -> bool:
        """Return True if Lambda has less than the configured threshold of time remaining."""
        if self._context is None:
            return False
        remaining_ms = self._context.get_remaining_time_in_millis()
        if remaining_ms < global_config.timeout_threshold_ms:
            logger.warning("Running low on time: %d ms remaining (threshold: %d ms)",
                           remaining_ms, global_config.timeout_threshold_ms)
            return True
        return False

    def _invoke_entry(
        self,
        entry: BatchEntry,
        config: types.GenerateContentConfig,
        tracker_entry: FixTrackerEntry | None = None,
    ) -> tuple[str, str, bool]:
        """Process a single entry, splitting into word chunks and persisting progress to S3/DynamoDB."""
        logger.info(f"Processing {entry.record_id} with Gemini")
        stem = entry.record_id

        chunks = self._split_by_words_static(entry.content, max_words=self._split_by_words_max)
        if len(chunks) > 1:
            logger.info(f"Split {stem} into {len(chunks)} word-chunks")

        resume_from = tracker_entry.current_chunk if tracker_entry else 1
        retry_number = tracker_entry.retry_number if tracker_entry else 1

        chunk_results = []
        for i in range(1, resume_from):
            existing = self._s3_client.get_object_content(
                self._temporary_fix_bucket, f"{stem}_{i}.txt"
            )
            if existing is None:
                logger.warning(f"  {stem}[chunk {i}/{len(chunks)}]: expected prior S3 result but found none, will reprocess")
                resume_from = i
                break
            logger.info(f"  {stem}[chunk {i}/{len(chunks)}]: loaded prior result from S3")
            chunk_results.append(existing)

        for i, chunk in enumerate(chunks, start=1):
            if i < resume_from:
                continue
            # Only use retry_number for the chunk we're resuming; subsequent chunks start fresh
            chunk_retry = retry_number if i == resume_from else 1

            label = f"{stem}[chunk {i}/{len(chunks)}]"

            if self._is_running_out_of_time():
                logger.warning(f"{stem}: stopping at chunk {i}/{len(chunks)} — time limit reached")
                raise TimeoutError(f"Time limit reached before chunk {i}/{len(chunks)} of {stem}")

            # Checkpoint current chunk position in DynamoDB before starting work
            if self._fix_tracker:
                self._fix_tracker.update_progress(
                    stem, i, len(chunks), self._get_time_remaining_ms()
                )

            if len(chunks) > 1:
                logger.info(f"  Processing chunk {i}/{len(chunks)} of {stem}")            
            
            fixed_chunk = self._invoke_chunk_with_retries(chunk, config, label, stem, i, chunk_retry, entry.context_files_bucket)
            chunk_results.append(fixed_chunk)

        fixed_text = "\n".join(chunk_results)
        original_word_count = len(entry.content.split())
        fixed_word_count = len(fixed_text.split())
        total_diff = abs(fixed_word_count - original_word_count)
        logger.info(
            f"Successfully processed {stem}: total word diff = {total_diff} "
            f"(original={original_word_count}, fixed={fixed_word_count})"
        )
        return stem, fixed_text, True

    def _invoke_chunk_with_retries(
        self,
        chunk: str,
        config: types.GenerateContentConfig,
        label: str,
        stem: str,
        split_idx: int,
        retry_number: int = 1,
        context_files_bucket: str = "",
    ) -> str:
        """Call Gemini for a single chunk, retrying up to 4 times on word count drift.

        Loads the best result from a prior lambda invocation (if any) from S3 as the
        starting baseline, then runs fresh attempts and overrides only when a better
        result is found.
        """
        max_retries = 4
        original_word_count = len(chunk.split())

        # Load best result written by any prior lambda invocation
        chunk_key = f"{stem}_{split_idx}.txt"
        if self._s3_client.file_exists(self._temporary_fix_bucket, chunk_key):
            existing_text = self._s3_client.get_object_content(
                self._temporary_fix_bucket, chunk_key
            )
            best_diff: float = abs(len(existing_text.split()) - original_word_count)
            best_text: str | None = existing_text
            logger.info(f"  {label}: loaded prior best from S3, diff={best_diff}")
            if best_diff <= self._max_word_diff:
                logger.info(f"  {label}: prior result already meets threshold, skipping Gemini")
                return best_text
        else:
            best_diff = float("inf")
            best_text = None

        start_attempt = retry_number
        for attempt in range(start_attempt, max_retries + 1):
            if self._is_running_out_of_time():
                logger.warning(f"{label}: stopping retries at attempt {attempt} — time limit reached")
                return best_text

            # Checkpoint retry position before each attempt
            if self._fix_tracker:
                self._fix_tracker.update_retry(
                    stem, attempt, self._get_time_remaining_ms(), self._invocation_started_at
                )

            attempt_config = config
            if attempt == max_retries and context_files_bucket:
                reasoning_config = self._get_reasoning_config(stem, context_files_bucket)
                if reasoning_config is not None:
                    attempt_config = reasoning_config
                    logger.info(f"{label}: last attempt — switching to reasoning config")

            try:
                fixed_text = self._call_gemini(chunk, attempt_config)
            except Exception as e:
                logger.warning(f"{label}: Gemini call failed on attempt {attempt}/{max_retries}: {e}")
                continue

            diff = abs(len(fixed_text.split()) - original_word_count)

            if diff < best_diff:
                best_diff = diff
                best_text = fixed_text
                self._s3_client.put_object_content(
                    self._temporary_fix_bucket, f"{stem}_{split_idx}.txt", fixed_text
                )

            if best_diff <= self._max_word_diff:
                break

            logger.warning(
                f"{label}: word diff {diff} exceeds {self._max_word_diff} "
                f"(original={original_word_count}, fixed={len(fixed_text.split())}), "
                f"attempt {attempt}/{max_retries}"
            )
        else:
            logger.warning(
                f"{label}: all attempts exhausted (retry_number={retry_number}, max_retries={max_retries}), "
                f"using best result with diff {best_diff}"
            )

        fixed_word_count = len(best_text.split()) if best_text else 0
        logger.info(
            f"{label}: word diff = {best_diff} "
            f"(original={original_word_count}, fixed={fixed_word_count})"
        )
        return best_text

    def _build_config(self, cache_name: Optional[str], system_prompt: str) -> types.GenerateContentConfig:
        """Build Gemini config with cache or system instruction fallback."""
        kwargs = {
            "temperature": self._temperature,
            "top_p": 0.1,
            "top_k": 1,
            "max_output_tokens": self._max_tokens,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if cache_name:
            kwargs["cached_content"] = cache_name
        else:
            kwargs["system_instruction"] = system_prompt
        return types.GenerateContentConfig(**kwargs)

    def _get_reasoning_config(self, stem: str, context_files_bucket: str) -> Optional[types.GenerateContentConfig]:
        """Fetch reasoning system prompt from S3 and build an uncached config with thinking enabled."""
        reasoning_key = f"{stem}.template.reasoning.txt"
        reasoning_prompt = self._s3_client.get_object_content(context_files_bucket, reasoning_key)
        if not reasoning_prompt:
            logger.warning(f"Reasoning template not found: s3://{context_files_bucket}/{reasoning_key}, using normal config")
            return None
        config = self._build_config(cache_name=None, system_prompt=reasoning_prompt)
        config.thinking_config = types.ThinkingConfig(thinking_budget=self._thinking_budget)
        return config

    def _call_gemini(self, content: str, config: types.GenerateContentConfig) -> str:
        """Send content to Gemini and return the response text."""
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=content,
            config=config,
        )
        # from types import SimpleNamespace
        # response = SimpleNamespace(text="1")
        if response.text is None:
            raise ValueError(
                f"Gemini returned empty response (finish_reason="
                f"{getattr(response.candidates[0], 'finish_reason', 'unknown') if response.candidates else 'no_candidates'})"
            )
        return response.text

    @staticmethod
    def _split_by_words_static(content: str, max_words: int = 5000) -> list[str]:
        """Split content into chunks of ~max_words, breaking at line boundaries."""
        lines = content.strip().split("\n")
        if not lines:
            return [content]

        total_words = len(content.split())
        if total_words <= max_words:
            return [content.strip()]

        chunks = []
        current_lines: list[str] = []
        current_word_count = 0

        for line in lines:
            line_words = len(line.split())
            if current_word_count + line_words > max_words and current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = [line]
                current_word_count = line_words
            else:
                current_lines.append(line)
                current_word_count += line_words

        if current_lines:
            chunks.append("\n".join(current_lines))

        return chunks

    def post_process(self, llm_response: list[tuple[str, str, bool]], original_files: list[BatchEntry]) -> ReviewResult:
        """Upload fixed text to S3, send SQS notification, clean up temp files and tracker."""
        fixed_count = 0
        failed_count = 0

        entry_by_stem = {entry.record_id: entry for entry in original_files}

        for stem, fixed_text, success in llm_response:
            if not success:
                failed_count += 1
                continue

            batch_entry = entry_by_stem.get(stem)
            transcription_bucket = batch_entry.transcription_bucket if batch_entry else ""
            output_bucket = batch_entry.output_bucket if batch_entry else ""

            try:
                if not self._s3_client.put_object_content(output_bucket, f"{stem}.txt", fixed_text):
                    failed_count += 1
                    continue

                time_key = f"{stem}.time"
                pre_fix_key = f"{stem}.pre-fix.time"
                self._s3_client.copy_object(transcription_bucket, time_key, output_bucket, pre_fix_key)

                self._s3_client.delete_objects_by_prefix(transcription_bucket, f"{stem}.")
                self._s3_client.delete_objects_by_prefix(self._temporary_fix_bucket, f"{stem}_")

                if self._fix_tracker:
                    self._fix_tracker.delete_entry(stem)

                try:
                    self._sqs_client.send_message(self._sqs_queue_url, {"media_id": stem})
                except Exception as e:
                    logger.error(f"SQS notification failed: {e}")

                fixed_count += 1
                logger.info(f"Successfully post-processed {stem}")

            except Exception as e:
                logger.error(f"Post-process failed for {stem}: {e}")
                failed_count += 1

        return ReviewResult(
            total_found=len(llm_response),
            fixed=fixed_count,
            failed=failed_count,
            batch_job_arn=None,
        )

    # --- Distributed locking / fix-tracker helpers ---

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _move_to_dead_letter(self, stem: str, transcription_bucket: str) -> None:
        """Move all {stem}.* files to dead-letter bucket and clean up temp + tracker."""
        dead_letter_bucket = global_config.dead_letter_bucket
        objects = self._s3_client.list_objects(transcription_bucket, prefix=f"{stem}.")
        for obj in objects:
            key = obj["Key"]
            self._s3_client.copy_object(transcription_bucket, key, dead_letter_bucket, key)
            self._s3_client.delete_object(transcription_bucket, key)
        if objects:
            logger.info("Moved %d files for %s to dead letter bucket %s", len(objects), stem, dead_letter_bucket)
        self._s3_client.delete_objects_by_prefix(self._temporary_fix_bucket, f"{stem}_")
        self._fix_tracker.delete_entry(stem)
        logger.info("Dead-lettered %s: cleaned up temp files and tracker entry", stem)

    def _resolve_tracker_entry(
        self,
        media_id: str,
        transcription_bucket: str,
        time_remaining_ms: int,
    ) -> tuple[FixTrackerEntry | None, bool]:
        """Claim or resume tracker entry for media_id.

        Returns (tracker_entry, should_skip).
        should_skip=True means the file must not be processed this invocation.
        """
        started_at = self._invocation_started_at
        claimed, existing = self._fix_tracker.try_claim(media_id, time_remaining_ms, started_at)

        if claimed:
            entry = FixTrackerEntry(
                media_id=media_id,
                retry_number=1,
                lambda_started_at=started_at,
                lambda_time_remaining_ms=time_remaining_ms,
                current_chunk=1,
                total_chunks=1,
            )
            return entry, False

        if existing is None:
            logger.error("Could not fetch existing tracker entry for %s, skipping", media_id)
            return None, True

        elapsed = existing.elapsed_seconds()

        if elapsed < global_config.max_lambda_age_seconds:
            logger.info(
                "Skipping %s: another lambda has been working on it for %.0f seconds",
                media_id, elapsed,
            )
            return None, True

        # Previous lambda crashed — always increment retry
        new_retry = existing.retry_number + 1

        if existing.lambda_time_remaining_ms > global_config.slow_llm_threshold_ms:
            logger.warning(
                "%s: previous lambda had %d ms remaining when it last wrote — LLM call was too slow",
                media_id, existing.lambda_time_remaining_ms,
            )
            if new_retry >= global_config.max_retries:
                logger.error(
                    "%s: reached max retries (%d), moving to dead letter bucket",
                    media_id, global_config.max_retries,
                )
                self._move_to_dead_letter(media_id, transcription_bucket)
                return None, True
        else:
            logger.info(
                "%s: previous lambda timed out normally (had %d ms remaining)",
                media_id, existing.lambda_time_remaining_ms,
            )

        self._fix_tracker.update_retry(media_id, new_retry, time_remaining_ms, started_at)
        logger.info("%s: retry %d/%d — resuming processing", media_id, new_retry, global_config.max_retries - 1)

        existing.retry_number = new_retry
        existing.lambda_started_at = started_at
        existing.lambda_time_remaining_ms = time_remaining_ms
        return existing, False

    # --- Gemini caching helpers ---

    def _get_or_create_cache(self, system_prompt: str) -> Optional[str]:
        """Get or create a cached content for the given system prompt.

        Returns the cache name, or None if caching failed.
        """
        normalized_prompt = "\n".join(line.strip() for line in system_prompt.strip().splitlines())

        if normalized_prompt in self._prompt_caches:
            cached_name, expiry = self._prompt_caches[normalized_prompt]
            if time.time() < (expiry - 300):
                logger.debug(f"Reusing existing cache: {cached_name}")
                return cached_name
            else:
                logger.info(f"Cache expired: {cached_name}")

        try:
            logger.info(f"Creating cache for system prompt: {system_prompt[:50]}...")
            prompt_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:8]

            cache_response = self._client.caches.create(
                model=self._model_name,
                config=types.CreateCachedContentConfig(
                    display_name=f"transcription_fix_{prompt_hash}",
                    system_instruction=system_prompt,
                    ttl="3600s",
                ),
            )

            cached_name = cache_response.name
            expiry = time.time() + 3600
            self._prompt_caches[normalized_prompt] = (cached_name, expiry)

            logger.info(f"Cache created: {cached_name}")
            return cached_name

        except Exception as e:
            logger.warning(f"Failed to create cache: {e}, falling back to uncached")
            return None

    def _count_tokens(self, content: str) -> int:
        """Count tokens using Gemini API."""
        try:
            response = self._client.models.count_tokens(
                model=self._model_name,
                contents=content,
            )
            return response.total_tokens
        except Exception as e:
            logger.warning(f"Token counting failed, using word estimate: {e}")
            word_count = len(content.split())
            return int(word_count * 4)
