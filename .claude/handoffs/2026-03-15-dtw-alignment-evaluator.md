# Session Handoff: DTW-based Alignment Evaluator for gpu_timestamp

**Date:** 2026-03-15
**Project:** C:\portal\transcription\gpu_timestamp
**Branch:** aws-fixes

## Current State

**Task:** Add DTW-based pre-alignment text fixing and evaluation to `alignment_evaluator.py`
**Phase:** Implementation
**Progress:** ~85% - Core code written, needs testing and potential refinement

## What We Did

Added a new `AlignmentEvaluator` class that runs banded DTW comparison between the pre-fix `.time` file and the LLM-corrected `.txt` file **before** calling `align_audio`. This fixes hallucinations in the corrected text and identifies a truncation cutoff point. The existing probability-based evaluation (rolling avg, CUSUM) runs after alignment. Truncation decision now uses rolling avg + DTW (CUSUM is still computed but only for informational output).

## Decisions Made

- **AlignmentEvaluator class** — Holds state across pre-alignment (DTW) and post-alignment (probability) phases in a single object
- **Pre-fix .time from TEXT_BUCKET** — Downloaded as `{stem}.pre-fix.time` from `portal-daf-yomi-fixed-text` (the TEXT_BUCKET), not from the transcription bucket
- **DTW fixes without truncation** — `pre_alignment_fix()` applies replacements to undo hallucinations but does NOT truncate the text; cutoff index is saved for later
- **CUSUM kept but not used for decisions** — Still computed and written to `.analysis` JSON, but truncation decision only uses `rolling_avg_index != -1 AND dtw_cutoff_index is not None`
- **Truncation point** — `max(rolling_avg_index, dtw_cutoff_index)` when both agree
- **Added dtw-python dependency** — External library for banded DTW computation

## Code Changes

**Files modified:**

- `gpu_timestamp/pyproject.toml` — Added `dtw-python>=1.3.0` dependency
- `gpu_timestamp/src/gpu_timestamp/services/alignment_evaluator.py` — Full rewrite: ported DTW functions from `C:\portal\match_time_series\banded_dtw.py`, added `AlignmentEvaluator` class with `pre_alignment_fix()` and `post_alignment_evaluate()` methods, kept all existing probability functions
- `gpu_timestamp/src/gpu_timestamp/handlers/alignment.py` — Updated `process_message()` to download `.pre-fix.time`, create evaluator, fix text before alignment, use new evaluation flow
- `gpu_timestamp/src/gpu_timestamp/services/aligner.py` — Updated `test_align_local_files()` to use `AlignmentEvaluator` class

**Key code context:**

The `AlignmentEvaluator` flow:
1. `evaluator = AlignmentEvaluator()`
2. `fixed_text = evaluator.pre_alignment_fix(time_content, text_content)` — DTW + fixes
3. `result = align_audio(audio_path, fixed_text, ...)` — stable-whisper alignment
4. `analysis = evaluator.post_alignment_evaluate(json_path)` — probability check
5. Truncate if `analysis["should_truncate"]` is True

## Open Questions

- [ ] Does `uv sync` install dtw-python cleanly in the GPU Docker environment?
- [ ] Performance: DTW distance matrix is O(n*m) — may be slow for very long transcriptions (5000+ words). May need optimization.
- [ ] The `.pre-fix.time` download uses `s3_downloader.download_text()` which reads from TEXT_BUCKET — verify this key pattern matches what post_inference creates

## Next Steps

1. [ ] Run `uv sync` in gpu_timestamp to install dtw-python
2. [ ] Test with a local file that has both `.pre-fix.time` and `.txt` using `test_align_local_files()`
3. [ ] Verify the `.analysis` JSON output contains all three methods (rolling_avg, cusum, dtw_cutoff)
4. [ ] Test edge cases: missing `.pre-fix.time` file, empty segments, short files
5. [ ] Consider Docker build — ensure dtw-python installs in the CUDA container

## Files to Review on Resume

- `gpu_timestamp/src/gpu_timestamp/services/alignment_evaluator.py` — Core new code (AlignmentEvaluator class + ported DTW functions)
- `gpu_timestamp/src/gpu_timestamp/handlers/alignment.py` — Updated pipeline orchestration
- `gpu_timestamp/src/gpu_timestamp/services/aligner.py` — Updated test function
- `C:\portal\match_time_series\banded_dtw.py` — Original DTW source (reference for any fixes needed)

## Reference: Source Material

The DTW functions were ported from `C:\portal\match_time_series\banded_dtw.py`. Key adaptations:
- File I/O functions changed to accept string content instead of file paths
- Logging changed from custom logger to standard `logging.getLogger(__name__)`
- Plotting and argparse removed
- `create_truncated_file` split into `_apply_replacements` (no truncation) used by `pre_alignment_fix`
