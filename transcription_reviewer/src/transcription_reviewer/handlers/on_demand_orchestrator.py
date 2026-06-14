"""On-demand orchestrator: per-file synchronous processing via LLMPipeline."""

import logging

from transcription_reviewer.config import config
from transcription_reviewer.models.review_orchestrator import ReviewOrchestrator, StartResult
from transcription_reviewer.models.schemas import TranscriptionFile
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.services.dynamo_reader import DynamoReader
from transcription_reviewer.services.s3_reader import S3Reader
from transcription_reviewer.services.transcription_fixer import TranscriptionFixer

logger = logging.getLogger(__name__)


def _is_running_out_of_time(context) -> bool:
    if context is None:
        return False
    remaining_ms = context.get_remaining_time_in_millis()
    threshold = config.timeout_threshold_ms
    if remaining_ms < threshold:
        logger.warning(
            "Running low on time: %d ms remaining (threshold: %d ms)",
            remaining_ms,
            threshold,
        )
        return True
    return False


class OnDemandOrchestrator(ReviewOrchestrator):
    """Per-file synchronous processing. Absorbs the process_transcriptions loop."""

    def __init__(
        self,
        s3_reader: S3Reader,
        pipeline: LLMPipeline,
        transcription_fixer: TranscriptionFixer,
        dynamo_reader: DynamoReader,
        bucket: str,
        context=None,
    ):
        self._s3_reader = s3_reader
        self._pipeline = pipeline
        self._transcription_fixer = transcription_fixer
        self._dynamo_reader = dynamo_reader
        self._bucket = bucket
        self._context = context

    def start(self) -> StartResult:
        logger.info("Processing transcriptions from s3://%s", self._bucket)

        transcriptions = self._s3_reader.list_transcriptions(
            bucket=self._bucket,
            prefix="",
            suffix=".txt",
        )

        if not transcriptions:
            logger.info("No transcription files found")
            return StartResult(mode="sync", total_found=0)

        logger.info("Found %d transcription files", len(transcriptions))

        fixed_count = 0
        failed_count = 0
        skipped_count = 0
        timed_out = False

        for trans in transcriptions:
            try:
                if _is_running_out_of_time(self._context):
                    timed_out = True
                    logger.info(
                        "Stopping early due to time limit. Processed %d/%d files.",
                        fixed_count + failed_count,
                        len(transcriptions),
                    )
                    break

                media_id = trans.stem

                dynamo_entry = self._dynamo_reader.get_entry(media_id)
                if not dynamo_entry:
                    logger.error("No DynamoDB entry for media_id=%s", media_id)
                    failed_count += 1
                    continue

                if dynamo_entry.status != "transcribed":
                    logger.info(
                        "Skipping %s: status=%s (expected 'transcribed')",
                        trans.stem,
                        dynamo_entry.status,
                    )
                    continue

                content = self._s3_reader.get_transcription_content(trans)
                if not content:
                    logger.error("Failed to read: %s", trans.key)
                    failed_count += 1
                    continue

                system_prompt = self._transcription_fixer.get_system_prompt(
                    trans.key,
                    template_bucket=dynamo_entry.context_files_bucket_s3,
                )
                if not system_prompt:
                    logger.error("Failed to get system prompt for: %s", trans.key)
                    failed_count += 1
                    continue

                line_count = len(content.strip().split("\n"))
                word_count = len(content.split())

                transcription_file = TranscriptionFile(
                    stem=trans.stem,
                    content=content,
                    system_prompt=system_prompt,
                    line_count=line_count,
                    word_count=word_count,
                    transcription_bucket=dynamo_entry.media_transcribed_bucket,
                    output_bucket=dynamo_entry.media_fixed_transcribed_bucket,
                    context_files_bucket=dynamo_entry.context_files_bucket_s3,
                )

                logger.info("Processing file: %s", trans.stem)

                logger.info("  Step 1: Preparing data...")
                prepared_data = self._pipeline.prepare_data(
                    [transcription_file], context=self._context
                )
                if not prepared_data:
                    logger.info("  Skipping %s (tracker signalled skip)", media_id)
                    skipped_count += 1
                    continue

                logger.info("  Step 2: Invoking LLM...")
                llm_response = self._pipeline.invoke(prepared_data, context=self._context)

                logger.info("  Step 3: Post-processing results...")
                result = self._pipeline.post_process(llm_response, prepared_data)

                fixed_count += result.fixed
                failed_count += result.failed

                if result.fixed > 0:
                    self._dynamo_reader.set_status(media_id, "fixed")

                logger.info("  Completed: fixed=%d, failed=%d", result.fixed, result.failed)

            except TimeoutError:
                logger.info("Time limit reached while processing %s", trans.key)
                timed_out = True
                break
            except Exception:
                logger.exception("Unexpected error processing %s", trans.key)
                failed_count += 1

        if skipped_count > 0:
            logger.info(
                "Skipped %d files (claimed by another lambda), will re-invoke to retry later",
                skipped_count,
            )
            timed_out = True

        return StartResult(
            mode="sync",
            total_found=len(transcriptions),
            fixed=fixed_count,
            failed=failed_count,
            timed_out=timed_out,
        )
