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
        temperature: float = 0.1,
        max_tokens: int = 60000,
        split_by_words: bool = True,
        split_by_words_max: int = 5000,
        max_word_diff: int = 100,
        thinking_budget: int = 1024,
    ):
        self._s3_client = s3_client
        self._sqs_client = sqs_client
        self._transcription_bucket = transcription_bucket
        self._output_bucket = output_bucket
        self._sqs_queue_url = sqs_queue_url
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._split_by_words = split_by_words
        self._split_by_words_max = split_by_words_max
        self._max_word_diff = max_word_diff
        self._thinking_budget = thinking_budget

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
        """Call Gemini for each entry using cached system prompts.
        
        """
        results = []

        for entry in prepared_data:
            try:
                cache_name = self._get_or_create_cache(entry.system_prompt)
                config = self._build_config(cache_name, entry.system_prompt)
                
                record_id, fixed_text, success = self._invoke_entry(entry, config)
                results.append((record_id, fixed_text, success))
            except Exception as e:
                logger.error(f"Failed to process {entry.record_id}: {e}")
                results.append((entry.record_id, "", False))

        return results

    def _invoke_entry(self, entry: BatchEntry, config: types.GenerateContentConfig) -> tuple[str, str, bool]:
        """Process a single entry, splitting into word chunks if enabled.

        Retries each chunk up to 4 times if the word count difference between
        the original and fixed chunk exceeds max_word_diff, keeping the best result.
        """
        logger.info(f"Processing {entry.record_id} with Gemini")

        if self._split_by_words:
            chunks = self._split_by_words_static(entry.content, max_words=self._split_by_words_max)
            if len(chunks) > 1:
                logger.info(f"Split {entry.record_id} into {len(chunks)} word-chunks")

            chunk_results = []
            for i, chunk in enumerate(chunks, start=1):
                if len(chunks) > 1:
                    logger.info(f"  Processing chunk {i}/{len(chunks)} of {entry.record_id}")

                fixed_chunk = self._invoke_chunk_with_retries(
                    chunk, config, f"{entry.record_id}[chunk {i}/{len(chunks)}]"
                )
                chunk_results.append(fixed_chunk)

            fixed_text = "\n".join(chunk_results)
        else:
            fixed_text = self._invoke_chunk_with_retries(
                entry.content, config, entry.record_id
            )

        logger.info(f"Successfully processed {entry.record_id}")
        return entry.record_id, fixed_text, True

    def _invoke_chunk_with_retries(self, chunk: str, config: types.GenerateContentConfig, label: str) -> str:
        """Call Gemini for a single chunk, retrying up to 4 times on word count drift."""
        max_retries = 4
        original_word_count = len(chunk.split())
        best_text: str | None = None
        best_diff = float("inf")

        for attempt in range(1, max_retries + 1):
            try:
                fixed_text = self._call_gemini(chunk, config)
            except Exception as e:
                logger.warning(f"{label}: Gemini call failed on attempt {attempt}/{max_retries}: {e}")
                continue

            diff = abs(len(fixed_text.split()) - original_word_count)

            if diff < best_diff:
                best_diff = diff
                best_text = fixed_text

            if diff <= self._max_word_diff:
                break

            logger.warning(
                f"{label}: word diff {diff} exceeds {self._max_word_diff} "
                f"(original={original_word_count}, fixed={len(fixed_text.split())}), "
                f"attempt {attempt}/{max_retries}"
            )
        else:
            logger.warning(
                f"{label}: all {max_retries} attempts exceeded word diff limit, "
                f"using best result with diff {best_diff}"
            )

        return best_text

    def _build_config(self, cache_name: Optional[str], system_prompt: str) -> types.GenerateContentConfig:
        """Build Gemini config with cache or system instruction fallback."""
        kwargs = {
            "temperature": self._temperature,
            "max_output_tokens": self._max_tokens,
            "thinking_config": types.ThinkingConfig(thinking_budget=self._thinking_budget),
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if cache_name:
            kwargs["cached_content"] = cache_name
        else:
            kwargs["system_instruction"] = system_prompt
        return types.GenerateContentConfig(**kwargs)

    def _call_gemini(self, content: str, config: types.GenerateContentConfig) -> str:
        """Send content to Gemini and return the response text."""
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=content,
            config=config,
        )
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
                # 1. Upload TXT file (fixed text for timestamps alignement)
                if not self._s3_client.put_object_content(
                    self._output_bucket, f"{stem}.txt", fixed_text
                ):
                    failed_count += 1
                    continue


                # Copy .time file as pre-fix transcription to output bucket before cleanup
                time_key = f"{stem}.time"
                pre_fix_key = f"{stem}.pre-fix.time"
                self._s3_client.copy_object(self._transcription_bucket, time_key, self._output_bucket, pre_fix_key)

                # Cleanup source files
                self._s3_client.delete_objects_by_prefix(self._transcription_bucket, f"{stem}.")                

                # Send SQS notification
                try:
                    self._sqs_client.send_message(
                        self._sqs_queue_url, {"filename": f"{stem}"}
                    )
                except Exception as e:
                    logger.error(f"SQS notification failed: {e}")

                fixed_count += 1
                logger.info(f"Successfully post-processed {stem}")

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
        """Split content by tokens if over limit.

        When split_by_words is enabled, word-based splitting happens in invoke(),
        so this always returns a single chunk. When disabled, this performs
        token-based splitting.
        """
        if self._split_by_words:
            return [(content.strip(), total_tokens)]

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
