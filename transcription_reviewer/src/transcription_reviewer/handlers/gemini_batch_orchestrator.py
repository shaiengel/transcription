"""Initial-trigger orchestrator: collect files, build JSONL, submit Gemini batch."""

import logging
from pathlib import Path

from transcription_reviewer.config import config
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.models.review_orchestrator import ReviewOrchestrator, StartResult
from transcription_reviewer.services.dynamo_reader import DynamoReader
from transcription_reviewer.services.gemini_batch_service import GeminiBatchService
from transcription_reviewer.services.s3_reader import S3Reader
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer
from transcription_reviewer.utils.batch_jsonl import BatchEntry
from transcription_reviewer.utils.text_utils import split_by_words

logger = logging.getLogger(__name__)


MAX_BATCH_FILES = 80


class GeminiBatchOrchestrator(ReviewOrchestrator):
    """Collect transcription files → split → submit Gemini batch."""

    def __init__(
        self,
        s3_reader: S3Reader,
        s3_client: S3Client,
        dynamo_reader: DynamoReader,
        transcription_fixer: TranscriptionFixer,
        batch_service: GeminiBatchService,
        bucket: str,
    ):
        self._s3_reader = s3_reader
        self._s3_client = s3_client
        self._dynamo_reader = dynamo_reader
        self._transcription_fixer = transcription_fixer
        self._svc = batch_service
        self._bucket = bucket

    def start(self) -> StartResult:
        logger.info("GeminiBatchOrchestrator: scanning s3://%s", self._bucket)

        # 1. List .txt files
        transcriptions = self._s3_reader.list_transcriptions(
            bucket=self._bucket, prefix="", suffix=".txt",
            max_items=MAX_BATCH_FILES,
        )
        if not transcriptions:
            logger.info("No transcription files found")
            return StartResult(mode="async", total_found=0)

        logger.info("Found %d transcription files", len(transcriptions))

        entries: list[BatchEntry] = []
        caches: dict[str, tuple[str, float]] = {}
        unique_prompts: set[str] = set()
        failed_count = 0

        for trans in transcriptions:
            stem = trans.stem
            media_id = stem

            # 2. DynamoDB gate
            dynamo_entry = self._dynamo_reader.get_entry(media_id)
            if not dynamo_entry:
                logger.error("No DynamoDB entry for media_id=%s", media_id)
                failed_count += 1
                continue

            if dynamo_entry.status != "transcribed":
                logger.info("Skipping %s: status=%s", stem, dynamo_entry.status)
                continue

            # Read content
            content = self._s3_reader.get_transcription_content(trans)
            if not content:
                logger.error("Failed to read: %s", trans.key)
                failed_count += 1
                continue

            # 3. Fetch system prompt
            system_prompt = self._transcription_fixer.get_system_prompt(
                trans.key, template_bucket=dynamo_entry.context_files_bucket_s3
            )
            if not system_prompt:
                logger.error("No system prompt for: %s", trans.key)
                failed_count += 1
                continue

            prompt_hash = GeminiBatchService.compute_prompt_hash(system_prompt)
            unique_prompts.add(system_prompt)

            # 4. Split into chunks
            max_words = dynamo_entry.max_word_split if dynamo_entry.max_word_split is not None else self._svc.split_by_words_max
            chunks = split_by_words(content, max_words=max_words)
            total_chunks = len(chunks)            

            for i, chunk in enumerate(chunks, start=1):
                chunk_media_id = f"{stem}_{i}" if total_chunks > 1 else stem
                chunk_word_count = len(chunk.split())

                # 5. Store original chunk to temp bucket
                self._s3_client.put_object_content(
                    bucket=self._svc.temporary_fix_bucket,
                    key=f"{chunk_media_id}.original.txt",
                    content=chunk,
                )

                # 6. Register in FIX_TRACKER_TABLE
                self._svc.register_fix_tracker_entry(
                    media_id=chunk_media_id,
                    stem=stem,
                    original_word_count=chunk_word_count,
                    total_chunks=total_chunks,
                )

                entries.append(
                    BatchEntry(
                        media_id=chunk_media_id,
                        system_prompt=system_prompt,
                        content=chunk,
                        prompt_hash=prompt_hash,
                    )
                )

        if not entries:
            logger.info("No entries to submit after filtering")
            return StartResult(mode="async", total_found=len(transcriptions), failed=failed_count)

        # 7. Create caches per unique system prompt
        for prompt in unique_prompts:
            self._svc.get_or_create_cache(prompt, caches)

        # 8-9. Build JSONL and upload to GCS
        gcs_input_file = self._svc.build_and_upload_jsonl(entries, caches)

        # 10. Submit batch job
        batch_job_name = self._svc.submit_batch(gcs_input_file)

        # 11. Store in BATCH_JOBS_TABLE (with caches for retrigger)
        self._svc.store_batch_job(batch_job_name, gcs_input_file, caches)

        logger.info(
            "Submitted batch: job=%s, entries=%d, failed=%d",
            batch_job_name,
            len(entries),
            failed_count,
        )

        return StartResult(
            mode="async",
            total_found=len(transcriptions),
            fixed=0,
            failed=failed_count,
            job_ref=batch_job_name,
        )
