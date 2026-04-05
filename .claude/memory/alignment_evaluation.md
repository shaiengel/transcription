# Alignment Evaluation

Source: `gpu_timestamp/src/gpu_timestamp/services/alignment_evaluator.py` → `AlignmentEvaluator`

## Two-Phase Evaluation

### Phase 1: Pre-alignment DTW (`pre_alignment_fix`)

Runs **before** stable-whisper. Compares original transcription (`.pre-fix.time`) with LLM-corrected text (`.txt`) to detect and fix hallucinations.

**Process:**
1. Tokenize both texts into words
2. Build word-level Levenshtein distance matrix
3. Run banded DTW alignment (slanted band)
4. Detect issues → compute cutoff → apply replacements

**Detection methods:**

| Method | What it detects | How |
|--------|----------------|-----|
| Vertical jumps | LLM hallucinated/duplicated content | Many original words map to same corrected word. Jumps > `jump_threshold` flagged |
| Score drop (moving avg) | LLM diverged from original | Per-segment match scores MA drops below `drop_threshold` |
| Score drop (word count) | Consecutive bad segments | Low-score segments accumulating 15+ words consecutively |

**Output:** `dtw_cutoff_index` (word position where corrected text stops being trustworthy) + replacements applied to fix hallucinations before cutoff.

### Phase 2: Post-alignment probability (`post_alignment_evaluate`)

Runs **after** stable-whisper. Uses word-level probabilities from forced alignment to detect where audio stops matching text.

| Method | What it detects | How |
|--------|----------------|-----|
| Rolling average | Audio-text mismatch | MA (window=100) of word probabilities drops below `rolling_avg_target` (fixed threshold, default 0.25) |
| CUSUM | Sustained quality shift | Cumulative negative deviation from 0.25 exceeds 50. **Informational only**, not used for truncation |

## Truncation Decision

- `should_truncate = True` if **either** DTW cutoff or rolling avg detects issues
- Truncate point: prefers `dtw_cutoff_index` (more precise), falls back to `rolling_avg_index`
- VTT and SRT files are truncated at that word position

## All Configurable Parameters

### DTW parameters (in `dtw` config section)

| Parameter | Env var | Default | Purpose |
|-----------|---------|---------|---------|
| `band_width` | `DTW_BAND_WIDTH` | 200 | Slanted band constraint width |
| `step_pattern` | `DTW_STEP_PATTERN` | `asymmetric` | DTW step pattern |
| `match_threshold` | `DTW_MATCH_THRESHOLD` | 0.5 | Good match distance threshold |
| `high_dist_threshold` | `DTW_HIGH_DIST_THRESHOLD` | 0.7 | Bad match grouping threshold |
| `low_score_threshold` | `DTW_LOW_SCORE_THRESHOLD` | 0.5 | Segment score for replacements |
| `jump_threshold` | `DTW_JUMP_THRESHOLD` | 40 | Min vertical jump size for cutoff |
| `drop_threshold` | `DTW_DROP_THRESHOLD` | 0.25 | MA threshold for quality drop |
| `ma_window` | `DTW_MA_WINDOW` | 10 | MA window for drop detection |

### Probability parameters (in `stable_whisper` config section)

| Parameter | Env var | Default | Purpose |
|-----------|---------|---------|---------|
| `rolling_avg_target` | `ROLLING_AVG_TARGET` | 0.25 | Fixed threshold for probability MA |
