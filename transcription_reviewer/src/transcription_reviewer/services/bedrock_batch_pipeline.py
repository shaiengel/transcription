"""Bedrock batch pipeline implementation."""

import json
import logging
import tempfile
import uuid
from pathlib import Path

from transcription_reviewer.infrastructure.bedrock_batch_client import BedrockBatchClient
from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.models.schemas import ReviewResult, TranscriptionFile
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.services.token_counter import TokenCounter
from transcription_reviewer.utils.batch_jsonl import BatchEntry

logger = logging.getLogger(__name__)


class BedrockBatchPipeline(LLMPipeline):
    """Bedrock batch inference pipeline."""

    def __init__(
        self,
        s3_client: S3Client,
        bedrock_batch_client: BedrockBatchClient,
        token_counter: TokenCounter,
        bucket: str,
        batch_model_id: str,
        batch_role_arn: str,
        min_entries: int = 100,
        max_tokens: int = 60000,
        temperature: float = 0.4,
    ):
        self._s3_client = s3_client
        self._bedrock_batch_client = bedrock_batch_client
        self._token_counter = token_counter
        self._bucket = bucket
        self._batch_model_id = batch_model_id
        self._batch_role_arn = batch_role_arn
        self._min_entries = min_entries
        self._max_tokens = max_tokens
        self._temperature = temperature

    def prepare_data(self, files: list[TranscriptionFile]) -> list[BatchEntry]:
        """Prepare batch entries with token counting and splitting."""
        entries: list[BatchEntry] = []

        for f in files:
            total_tokens = self._token_counter.count_content_tokens(f.content)
            logger.info(f"File {f.stem}: {total_tokens} tokens")

            chunks = self._split_content(f.content, total_tokens)
            if len(chunks) > 1:
                logger.info(f"Split {f.stem} into {len(chunks)} chunks")

            for i, (chunk_content, chunk_tokens) in enumerate(chunks, start=1):
                record_id = f.stem if len(chunks) == 1 else f"{f.stem}_{i}"
                entries.append(
                    BatchEntry(
                        record_id=record_id,
                        system_prompt=f.system_prompt,
                        content=chunk_content,
                        token_count=chunk_tokens,
                    )
                )

        # Pad to MIN_ENTRIES
        for i in range(len(entries), self._min_entries):
            entries.append(
                BatchEntry(
                    record_id=f"dummy_{i}",
                    system_prompt="ok",
                    content="ok",
                    token_count=2,
                )
            )

        logger.info(f"Prepared {len(entries)} batch entries")
        return entries

    def invoke(self, prepared_data: list[BatchEntry]) -> str | None:
        """Submit batch job to Bedrock."""
        job_id = str(uuid.uuid4())[:8]
        job_name = f"transcription-fix-{job_id}"
        input_key = f"batch-input/{job_name}.jsonl"

        if not self._batch_role_arn:
            logger.error("BATCH_ROLE_ARN not set")
            return None

        # Create and upload JSONL
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / f"{job_name}.jsonl"
            self._create_jsonl(prepared_data, tmp_path)

            if not self._s3_client.upload_file(tmp_path, self._bucket, input_key):
                logger.error("Failed to upload batch input")
                return None

        # Submit batch job
        job_arn = self._bedrock_batch_client.create_batch_job(
            job_name=job_name,
            model_id=self._batch_model_id,
            role_arn=self._batch_role_arn,
            input_s3_uri=f"s3://{self._bucket}/{input_key}",
            output_s3_uri=f"s3://{self._bucket}/batch-output/{job_name}/",
        )

        if job_arn:
            logger.info(f"Created batch job: {job_arn}")
        return job_arn

    def post_process(self, llm_response: str | None, original_files: list[TranscriptionFile]) -> ReviewResult:
        """Post-processing handled by post_inference Lambda (async)."""
        if llm_response:
            logger.info("Batch job submitted, post_inference will handle results")
            return ReviewResult(
                total_found=len(original_files),
                fixed=0,
                failed=0,
                batch_job_arn=llm_response,
            )
        else:
            return ReviewResult(
                total_found=len(original_files),
                fixed=0,
                failed=len(original_files),
                batch_job_arn=None,
            )

    def _split_content(self, content: str, total_tokens: int) -> list[tuple[str, int]]:
        """Split content by tokens."""
        lines = content.strip().split("\n")
        if not lines or total_tokens <= self._max_tokens:
            return [(content.strip(), total_tokens)]

        tokens_per_line = total_tokens / len(lines)
        chunks = []
        current_lines = []
        current_tokens = 0.0

        for line in lines:
            if current_tokens + tokens_per_line > self._max_tokens and current_lines:
                chunks.append(("\n".join(current_lines), int(current_tokens)))
                current_lines = [line]
                current_tokens = tokens_per_line
            else:
                current_lines.append(line)
                current_tokens += tokens_per_line

        if current_lines:
            chunks.append(("\n".join(current_lines), int(current_tokens)))

        return chunks

    def _create_jsonl(self, entries: list[BatchEntry], output_path: Path):
        """Create JSONL file."""
        with open(output_path, "w", encoding="utf-8") as f:
            for entry in entries:
                record = {
                    "recordId": entry.record_id,
                    "modelInput": {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": self._max_tokens,
                        "temperature": self._temperature,
                        "system": entry.system_prompt,
                        "messages": [{"role": "user", "content": entry.content}],
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
