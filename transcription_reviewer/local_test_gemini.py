"""Local test script for GeminiPipeline — reads from local disk, no AWS required."""

import json
import logging
from pathlib import Path

from transcription_reviewer.config import config
from transcription_reviewer.models.schemas import TranscriptionFile, ReviewResult
from transcription_reviewer.services.gemini_pipeline import GeminiPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TXT_FILE = Path("C:/Users/z0050yye/Downloads/303598.txt")
TEMPLATE_FILE = Path("C:/Users/z0050yye/Downloads/303598.template.txt")
OUTPUT_DIR = Path("C:/Users/z0050yye/Downloads")


class MockS3Client:
    """S3Client replacement that reads/writes local disk."""

    def put_object_content(self, bucket: str, key: str, content: str) -> bool:
        out_path = OUTPUT_DIR / key
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        logger.info("[MockS3] Written: %s", out_path)
        return True

    def copy_object(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        logger.info("[MockS3] copy_object skipped locally: %s -> %s", src_key, dst_key)

    def delete_objects_by_prefix(self, bucket: str, prefix: str) -> None:
        logger.info("[MockS3] delete_objects_by_prefix skipped locally: %s/%s", bucket, prefix)

    def get_object_content(self, bucket: str, key: str) -> str | None:
        logger.info("[MockS3] get_object_content not expected in local test: %s/%s", bucket, key)
        return None


class MockSQSClient:
    """SQSClient replacement that just logs."""

    def send_message(self, queue_url: str, message: dict) -> None:
        logger.info("[MockSQS] send_message: %s", json.dumps(message))


def main():
    if not config.google_api_key:
        raise ValueError("GOOGLE_API_KEY is not set — check .env or config.secrets.dev.json")

    content = TXT_FILE.read_text(encoding="utf-8")
    system_prompt = TEMPLATE_FILE.read_text(encoding="utf-8")
    stem = TXT_FILE.stem

    transcription_file = TranscriptionFile(
        stem=stem,
        content=content,
        system_prompt=system_prompt,
        line_count=len(content.strip().split("\n")),
        word_count=len(content.split()),
    )
    logger.info("Loaded: %s (%d lines, %d words)", stem, transcription_file.line_count, transcription_file.word_count)

    pipeline = GeminiPipeline(
        s3_client=MockS3Client(),
        sqs_client=MockSQSClient(),
        api_key=config.google_api_key,
        transcription_bucket="local-transcription",
        output_bucket="local-output",
        sqs_queue_url="local-sqs",
        model_name=config.gemini_model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        split_by_words=config.split_by_words,
        split_by_words_max=config.split_by_words_max,
        max_word_diff=config.max_word_diff,
        thinking_budget=config.thinking_budget,
    )

    logger.info("Processing file: %s", stem)

    logger.info("  Step 1: Preparing data...")
    prepared_data = pipeline.prepare_data([transcription_file])

    logger.info("  Step 2: Invoking LLM...")
    llm_response = pipeline.invoke(prepared_data)

    logger.info("  Step 3: Post-processing results...")
    result: ReviewResult = pipeline.post_process(llm_response, prepared_data)

    print("\n" + "=" * 50)
    print(f"Result: fixed={result.fixed}, failed={result.failed}")
    print(f"Output written to: {OUTPUT_DIR}")
    print("=" * 50)


if __name__ == "__main__":
    main()
