"""Result-trigger orchestrator: fetch batch results, evaluate diffs, retry or finalize."""

import logging

from transcription_reviewer.config import config
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.infrastructure.sqs_client import SQSClient
from transcription_reviewer.models.review_orchestrator import ReviewOrchestrator, StartResult
from transcription_reviewer.services.dynamo_reader import DynamoReader
from transcription_reviewer.services.gemini_batch_service import GeminiBatchService
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer
from transcription_reviewer.utils.batch_jsonl import BatchEntry
from transcription_reviewer.utils.text_utils import word_count_diff

logger = logging.getLogger(__name__)


class GeminiBatchRetriggerOrchestrator(ReviewOrchestrator):
    """Fetch Gemini batch results → diff eval → retry or finalize."""

    def __init__(
        self,
        s3_client: S3Client,
        sqs_client: SQSClient,
        dynamo_reader: DynamoReader,
        transcription_fixer: TranscriptionFixer,
        batch_service: GeminiBatchService,
        batch_job_id: str,
    ):
        self._s3_client = s3_client
        self._sqs_client = sqs_client
        self._dynamo_reader = dynamo_reader
        self._transcription_fixer = transcription_fixer
        self._svc = batch_service
        self._batch_job_id = batch_job_id

    def start(self) -> StartResult:
        logger.info("Retrigger: processing batch_job_id=%s", self._batch_job_id)

        # --- Retrieval ---

        # 1. Look up batch job (includes persisted caches)
        batch_job = self._svc.get_batch_job(self._batch_job_id)
        if not batch_job:
            logger.error("Batch job not found: %s", self._batch_job_id)
            return StartResult(mode="completed", failed=1)

        caches = self._svc.extract_caches(batch_job)
        gcs_input_file = batch_job.get("gcs_input_file", {}).get("S", "")
        output_file_uri = batch_job.get("output_file_uri", {}).get("S", "")
        if not output_file_uri:
            logger.error("No output_file_uri for batch %s", self._batch_job_id)
            return StartResult(mode="completed", failed=1)

        # 2. Download and parse results from output file
        results = self._svc.fetch_batch_results(output_file_uri)
        logger.info("Fetched %d results from output file: %s", len(results), output_file_uri)

        # --- Evaluation ---

        retry_entries: list[BatchEntry] = []
        reasoning_media_ids: set[str] = set()
        dead_lettered_stems: set[str] = set()
        fixed_count = 0
        failed_count = 0

        for result in results:
            media_id = result.get("key", "")
            if not media_id:
                continue

            stem = self._extract_stem(media_id)

            if stem in dead_lettered_stems:
                continue

            # 5. Get tracker entry
            tracker = self._svc.get_fix_tracker(media_id)
            if not tracker:
                logger.warning("No tracker for %s, skipping", media_id)
                continue

            original_word_count = int(tracker.get("original_word_count", {}).get("N", "0"))
            stored_diff = int(tracker.get("diff_value", {}).get("N", "999999"))
            retry_number = int(tracker.get("retry_number", {}).get("N", "1"))
            tracker_stem = tracker.get("stem", {}).get("S", stem)

            # 6. Check for errors
            error = result.get("error")
            response = result.get("response")
            response_text = self._extract_response_text(response) if response else None

            if error or response_text is None:
                logger.error(
                    "Error for %s: %s", media_id, error or "empty response"
                )
                self._svc.dead_letter_media(tracker_stem, config.transcription_bucket)
                dead_lettered_stems.add(tracker_stem)
                failed_count += 1
                continue

            # Calculate diff
            new_diff = word_count_diff(response_text, original_word_count)
            is_last_retry = retry_number >= self._svc.max_retries

            if new_diff <= self._svc.max_word_diff:
                # GOOD — write result, mark completed
                self._s3_client.put_object_content(
                    self._svc.temporary_fix_bucket, f"{media_id}.txt", response_text
                )
                self._svc.update_fix_tracker(
                    media_id, completed=True, diff_value=new_diff
                )
                logger.info("Chunk %s completed: diff=%d", media_id, new_diff)

            elif new_diff < stored_diff:
                # BETTER but not good enough
                self._s3_client.put_object_content(
                    self._svc.temporary_fix_bucket, f"{media_id}.txt", response_text
                )
                self._svc.update_fix_tracker(
                    media_id, diff_value=new_diff, retry_number=retry_number + 1
                )

                if is_last_retry:
                    logger.info("Chunk %s: better (diff=%d) but last retry, accepting", media_id, new_diff)
                    self._svc.update_fix_tracker(media_id, completed=True)
                else:
                    logger.info("Chunk %s: better (diff=%d→%d), retrying", media_id, stored_diff, new_diff)
                    retry_entry = self._build_retry_entry(media_id, tracker_stem, retry_number + 1)
                    if retry_entry:
                        retry_entries.append(retry_entry)
                        if retry_number + 1 >= self._svc.max_retries:
                            reasoning_media_ids.add(media_id)

            else:
                # WORSE or equal — keep existing
                self._svc.update_fix_tracker(
                    media_id, retry_number=retry_number + 1
                )

                if is_last_retry:
                    logger.info("Chunk %s: worse/equal (diff=%d), last retry, accepting stored", media_id, new_diff)
                    self._svc.update_fix_tracker(media_id, completed=True)
                else:
                    logger.info("Chunk %s: worse/equal (diff=%d vs %d), retrying", media_id, new_diff, stored_diff)
                    retry_entry = self._build_retry_entry(media_id, tracker_stem, retry_number + 1)
                    if retry_entry:
                        retry_entries.append(retry_entry)
                        if retry_number + 1 >= self._svc.max_retries:
                            reasoning_media_ids.add(media_id)

        # --- Retry or Finalize ---

        if retry_entries:
            result = self._submit_retry_batch(retry_entries, reasoning_media_ids, fixed_count, failed_count, caches)
        else:
            result = self._finalize(fixed_count, failed_count, dead_lettered_stems, caches)

        # Cleanup: GCS input file, output file, Gemini batch, DynamoDB record
        if gcs_input_file:
            self._svc.delete_gcs_file(gcs_input_file)
        # can't be removed as the file is longer than 40 chars. it will be removed with the gemini batch cleanup.
        # if output_file_uri:
        #     self._svc.delete_gcs_file(output_file_uri)
        self._svc.delete_gemini_batch(self._batch_job_id)
        self._svc.delete_batch_job_record(self._batch_job_id)

        return result

    def _submit_retry_batch(
        self,
        retry_entries: list[BatchEntry],
        reasoning_media_ids: set[str],
        fixed_count: int,
        failed_count: int,
        caches: dict,
    ) -> StartResult:
        """Submit retry batch with remaining entries."""
        seen_hashes: set[str] = set()
        for entry in retry_entries:
            if entry.prompt_hash not in seen_hashes and entry.media_id not in reasoning_media_ids:
                self._svc.get_or_create_cache(entry.system_prompt, caches)
                seen_hashes.add(entry.prompt_hash)

        gcs_input_file = self._svc.build_and_upload_jsonl(
            retry_entries, caches, reasoning_media_ids
        )
        batch_job_name = self._svc.submit_batch(gcs_input_file)
        self._svc.store_batch_job(batch_job_name, gcs_input_file, caches)

        logger.info("Submitted retry batch: job=%s, entries=%d", batch_job_name, len(retry_entries))

        return StartResult(
            mode="async",
            fixed=fixed_count,
            failed=failed_count,
            job_ref=batch_job_name,
        )

    def _finalize(
        self, fixed_count: int, failed_count: int, dead_lettered_stems: set[str], caches: dict
    ) -> StartResult:
        """All chunks done — merge, upload, notify, clean up."""
        completed = self._svc.collect_completed_stems()
        logger.info("Finalizing %d stems", len(completed))

        for stem, media_ids in completed.items():
            if stem in dead_lettered_stems:
                continue

            dynamo_entry = self._dynamo_reader.get_entry(stem)
            if not dynamo_entry:
                logger.error("No DynamoDB entry for stem=%s during finalize", stem)
                failed_count += 1
                continue

            try:
                # Merge chunks
                merged = self._merge_chunks(stem, media_ids)
                if not merged:
                    logger.error("Failed to merge chunks for %s", stem)
                    failed_count += 1
                    continue

                # Upload to output bucket
                output_bucket = dynamo_entry.media_fixed_transcribed_bucket
                if not self._s3_client.put_object_content(output_bucket, f"{stem}.txt", merged):
                    failed_count += 1
                    continue

                # Copy .time → .pre-fix.time
                transcription_bucket = dynamo_entry.media_transcribed_bucket
                self._s3_client.copy_object(
                    transcription_bucket, f"{stem}.time",
                    output_bucket, f"{stem}.pre-fix.time",
                )

                # SQS notification
                self._sqs_client.send_message(
                    config.sqs_queue_url, {"media_id": stem}
                )

                # Set status to "fixed"
                self._dynamo_reader.set_status(stem, "fixed")

                # Clean up source files
                self._s3_client.delete_objects_by_prefix(transcription_bucket, f"{stem}.")

                # Clean up temp files and tracker
                self._s3_client.delete_objects_by_prefix(self._svc.temporary_fix_bucket, f"{stem}")
                self._svc.delete_fix_tracker_by_stem(stem)

                fixed_count += 1
                logger.info("Finalized %s", stem)

            except Exception:
                logger.exception("Failed to finalize stem %s", stem)
                failed_count += 1

        self._svc.delete_prompt_caches(caches)

        return StartResult(
            mode="completed",
            fixed=fixed_count,
            failed=failed_count,
        )

    def _merge_chunks(self, stem: str, media_ids: list[str]) -> str | None:
        """Merge split chunks into single text. Returns merged content or None."""
        if len(media_ids) == 1 and media_ids[0] == stem:
            # Unsplit file
            return self._s3_client.get_object_content(
                self._svc.temporary_fix_bucket, f"{stem}.txt"
            )

        # Sort by chunk index: {stem}_{1}, {stem}_{2}, ...
        sorted_ids = sorted(media_ids, key=lambda mid: self._chunk_index(mid, stem))

        parts = []
        for mid in sorted_ids:
            content = self._s3_client.get_object_content(
                self._svc.temporary_fix_bucket, f"{mid}.txt"
            )
            if content is None:
                logger.error("Missing chunk %s.txt in temp bucket", mid)
                return None
            parts.append(content.strip())

        return "\n".join(parts)

    def _build_retry_entry(self, media_id: str, stem: str, retry_number: int) -> BatchEntry | None:
        """Build a retry BatchEntry using the ORIGINAL chunk (never prior LLM output)."""
        original = self._s3_client.get_object_content(
            self._svc.temporary_fix_bucket, f"{media_id}.original.txt"
        )
        if not original:
            logger.error("Missing original chunk for %s", media_id)
            return None

        # On last retry, use reasoning prompt
        is_last = retry_number >= self._svc.max_retries
        dynamo_entry = self._dynamo_reader.get_entry(stem)
        if not dynamo_entry:
            logger.error("No DynamoDB entry for stem=%s", stem)
            return None

        if is_last:
            reasoning_key = f"{stem}.template.reasoning.txt"
            system_prompt = self._s3_client.get_object_content(
                dynamo_entry.context_files_bucket_s3, reasoning_key
            )
            if not system_prompt:
                system_prompt = self._transcription_fixer.get_system_prompt(
                    f"{stem}.txt", template_bucket=dynamo_entry.context_files_bucket_s3
                )
        else:
            system_prompt = self._transcription_fixer.get_system_prompt(
                f"{stem}.txt", template_bucket=dynamo_entry.context_files_bucket_s3
            )

        if not system_prompt:
            logger.error("No system prompt for retry of %s", media_id)
            return None

        return BatchEntry(
            media_id=media_id,
            system_prompt=system_prompt,
            content=original,
            prompt_hash=GeminiBatchService.compute_prompt_hash(system_prompt),
        )

    @staticmethod
    def _extract_stem(media_id: str) -> str:
        """Extract stem from media_id: '151415_2' → '151415', '151415' → '151415'."""
        parts = media_id.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0]
        return media_id

    @staticmethod
    def _chunk_index(media_id: str, stem: str) -> int:
        """Extract chunk index: '{stem}_{3}' → 3, '{stem}' → 0."""
        if media_id == stem:
            return 0
        suffix = media_id[len(stem) + 1:]
        try:
            return int(suffix)
        except ValueError:
            return 0

    @staticmethod
    def _extract_response_text(response) -> str | None:
        """Extract text from Gemini batch response object."""
        try:
            if hasattr(response, "candidates"):
                for candidate in response.candidates:
                    if hasattr(candidate, "content") and candidate.content:
                        parts = candidate.content.parts
                        texts = []
                        for part in parts:
                            if hasattr(part, "text") and part.text:
                                texts.append(part.text)
                        if texts:
                            return "\n".join(texts)
            if isinstance(response, dict):
                candidates = response.get("candidates", [])
                for candidate in candidates:
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])
                    texts = [p.get("text", "") for p in parts if p.get("text")]
                    if texts:
                        return "\n".join(texts)
        except Exception as e:
            logger.warning("Failed to extract response text: %s", e)
        return None
