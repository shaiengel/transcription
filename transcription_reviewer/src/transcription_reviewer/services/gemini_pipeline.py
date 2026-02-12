"""Gemini pipeline implementation with system prompt caching."""

import hashlib
import logging
import re
import time
from typing import Optional

from google import genai
from google.genai import types

from transcription_reviewer.infrastructure.s3_client import S3Client
from transcription_reviewer.infrastructure.sqs_client import SQSClient
from transcription_reviewer.models.schemas import ReviewResult, TranscriptionFile
from transcription_reviewer.models.llm_pipeline import LLMPipeline
from transcription_reviewer.utils.batch_jsonl import BatchEntry
from transcription_reviewer.utils.vtt_converter import convert_to_vtt

logger = logging.getLogger(__name__)

TIMED_LINE_PATTERN = re.compile(
    r"^(\[\d+\]\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+-\s+\d{2}:\d{2}:\d{2}\.\d{3}:\s*)(.*)$"
)


class GeminiPipeline(LLMPipeline):
    """Gemini API pipeline with per-file system prompts and caching."""

    def __init__(
        self,
        s3_client: S3Client,
        sqs_client: SQSClient,
        api_key: str,
        transcription_bucket: str,
        output_bucket: str,
        sqs_queue_url: str,
        model_name: str = "gemini-2.5-flash",
        temperature: float = 0.4,
        max_tokens: int = 60000,
    ):
        self._s3_client = s3_client
        self._sqs_client = sqs_client
        self._transcription_bucket = transcription_bucket
        self._output_bucket = output_bucket
        self._sqs_queue_url = sqs_queue_url
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens

        # Create client
        self._client = genai.Client(api_key=api_key)

        # Cache management: maps system_prompt -> (cached_content_name, expiry_time)
        self._prompt_caches: dict[str, tuple[str, float]] = {}

    def prepare_data(self, files: list[TranscriptionFile]) -> list[BatchEntry]:
        """Prepare batch entries with token counting and splitting."""
        entries: list[BatchEntry] = []

        for f in files:
            # Count tokens using Gemini API
            total_tokens = self._count_tokens(f.content)
            logger.info(f"File {f.stem}, words {len(f.content.split())}: {total_tokens} tokens")

            # Split if over the limit
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

        logger.info(f"Prepared {len(entries)} batch entries for Gemini")
        return entries

    def invoke(self, prepared_data: list[BatchEntry]) -> list[tuple[str, str, bool]]:
        """Call Gemini for each entry using cached system prompts."""
        results = []

        for entry in prepared_data:
            try:
                logger.info(f"Processing {entry.record_id} with Gemini")

                # Get or create cache for this system prompt
                cache_name = self._get_or_create_cache(entry.system_prompt)

                # Build config with or without cache
                if cache_name:
                    config = types.GenerateContentConfig(
                        temperature=self._temperature,
                        max_output_tokens=self._max_tokens,
                        cached_content=cache_name,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=1024  # number of thinking tokens (0 = off)
                        ),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True  # or max_remote_calls=5
                        )
                    )
                else:
                    # Fallback: no cache, use system_instruction directly
                    config = types.GenerateContentConfig(
                        temperature=self._temperature,
                        max_output_tokens=self._max_tokens,
                        system_instruction=entry.system_prompt,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=1024  # number of thinking tokens (0 = off)
                        ),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True  # or max_remote_calls=5
                        )
                    )

                # Generate with model name (not cache name)
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=entry.content,
                    config=config,
                )

                fixed_text = response.text
                results.append((entry.record_id, fixed_text, True))
                logger.info(f"Successfully processed {entry.record_id}")

            except Exception as e:
                logger.error(f"Failed to process {entry.record_id}: {e}")
                results.append((entry.record_id, "", False))

        return results

    def post_process(self, llm_response: list[tuple[str, str, bool]], original_files: list[BatchEntry]) -> ReviewResult:
        """Match timestamps, create VTT, upload to S3, send SQS."""
        fixed_count = 0
        failed_count = 0

        # Group split records back together (stem_1, stem_2 -> stem)
        grouped_results = self._group_split_records(llm_response)

        for stem, fixed_text, success in grouped_results:
            if not success:
                failed_count += 1
                continue

            try:
                # 1. Upload TXT file (fixed text for RAG)
                if not self._s3_client.put_object_content(
                    self._output_bucket, f"{stem}.txt", fixed_text
                ):
                    failed_count += 1
                    continue

                # 2. Read .time file with timestamps
                time_key = f"{stem}.time"
                timed_content = self._s3_client.get_object_content(
                    self._transcription_bucket, time_key
                )

                if timed_content:
                    # 3. Inject timestamps into fixed text
                    timed_fixed = self._inject_timestamps(fixed_text, timed_content)

                    if timed_fixed:
                        # 4. Convert to VTT and upload
                        vtt_content = convert_to_vtt(timed_fixed)
                        self._s3_client.put_object_content(
                            self._output_bucket, f"{stem}.vtt", vtt_content
                        )
                    else:
                        # Line count mismatch - use original timed content for VTT
                        logger.warning(f"Line mismatch for {stem}, using original timing")
                        vtt_content = convert_to_vtt(timed_content)
                        self._s3_client.put_object_content(
                            self._output_bucket, f"{stem}.vtt", vtt_content
                        )
                        # Also save the fixed text without timing
                        self._s3_client.put_object_content(
                            self._output_bucket, f"{stem}.no_timing.txt", fixed_text
                        )

                    # 5. Copy .time as .pre-fix.time (backup)
                    self._s3_client.put_object_content(
                        self._output_bucket, f"{stem}.pre-fix.time", timed_content
                    )

                # 6. Send SQS notification
                try:
                    self._sqs_client.send_message(
                        self._sqs_queue_url, {"filename": f"{stem}"}
                    )
                except Exception as e:
                    logger.error(f"SQS notification failed: {e}")

                fixed_count += 1
                logger.info(f"Successfully processed {stem}")

            except Exception as e:
                logger.error(f"Post-process failed for {stem}: {e}")
                failed_count += 1

        return ReviewResult(
            total_found=len(grouped_results),
            fixed=fixed_count,
            failed=failed_count,
            batch_job_arn=None,
        )

    def _group_split_records(self, llm_response: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
        """Group split records (stem_1, stem_2) back into single stem."""
        from collections import defaultdict

        # Group by stem (remove _N suffix)
        groups: dict[str, list[tuple[int, str, bool]]] = defaultdict(list)

        for record_id, fixed_text, success in llm_response:
            # Extract stem and part number
            if "_" in record_id and record_id.rsplit("_", 1)[1].isdigit():
                stem = record_id.rsplit("_", 1)[0]
                part_num = int(record_id.rsplit("_", 1)[1])
            else:
                stem = record_id
                part_num = 1

            groups[stem].append((part_num, fixed_text, success))

        # Merge splits
        merged = []
        for stem, parts in groups.items():
            # Sort by part number
            parts.sort(key=lambda x: x[0])

            # Check if all parts succeeded
            all_success = all(success for _, _, success in parts)

            # Merge text
            merged_text = "\n".join(text for _, text, _ in parts if text)

            merged.append((stem, merged_text, all_success))

        return merged

    def _get_or_create_cache(self, system_prompt: str) -> Optional[str]:
        """Get or create a cached content for the given system prompt.

        Returns the cache name to pass to cached_content, or None if caching failed.
        """
        # Normalize prompt for cache key (strip whitespace, normalize line endings)
        normalized_prompt = "\n".join(line.strip() for line in system_prompt.strip().splitlines())

        # Check if we have a valid cache for this prompt
        if normalized_prompt in self._prompt_caches:
            cached_name, expiry = self._prompt_caches[normalized_prompt]
            # Check if cache is still valid (5 min buffer before expiry)
            if time.time() < (expiry - 300):
                logger.debug(f"Reusing existing cache: {cached_name}")
                return cached_name
            else:
                logger.info(f"Cache expired: {cached_name}")

        # Create new cache
        try:
            logger.info(f"Creating cache for system prompt: {system_prompt[:50]}...")

            # Hash the prompt for a short display name
            prompt_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:8]

            cache_response = self._client.caches.create(
                model=self._model_name,
                config=types.CreateCachedContentConfig(
                    display_name=f"transcription_fix_{prompt_hash}",
                    system_instruction=system_prompt,
                    ttl="3600s",  # 1 hour
                ),
            )

            cached_name = cache_response.name
            expiry = time.time() + 3600
            self._prompt_caches[normalized_prompt] = (cached_name, expiry)

            logger.info(f"Cache created: {cached_name}")
            return cached_name

        except Exception as e:
            logger.warning(f"Failed to create cache: {e}, falling back to uncached")
            # Return None to signal fallback to system_instruction
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
            # Fallback to word-based estimation (4x for Hebrew text)
            word_count = len(content.split())
            return int(word_count * 4)

    def _split_content(self, content: str, total_tokens: int) -> list[tuple[str, int]]:
        """Split content by tokens if over limit."""
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

    def _inject_timestamps(self, fixed_text: str, timed_content: str) -> str | None:
        """Inject timestamps from timed_content into fixed_text."""
        fixed_lines = [l.strip() for l in fixed_text.strip().split("\n") if l.strip()]
        timed_lines = [l.strip() for l in timed_content.strip().split("\n") if l.strip()]

        if len(fixed_lines) != len(timed_lines):
            logger.warning(f"Line mismatch: {len(fixed_lines)} vs {len(timed_lines)}")
            return None

        result = []
        for fixed_line, timed_line in zip(fixed_lines, timed_lines):
            match = TIMED_LINE_PATTERN.match(timed_line)
            if match:
                result.append(f"{match.group(1)}{fixed_line}")
            else:
                result.append(fixed_line)

        return "\n".join(result)
