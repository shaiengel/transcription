# DTW Step Patterns

Source: `alignment_evaluator.py` → `_banded_dtw_alignment()`

## Chosen Pattern: `asymmetric`

We use `asymmetric` as the default because:
- The prefix (original) and corrected files have **different lengths** (~500 word difference)
- Asymmetric allows the reference to advance without consuming query words, handling length mismatches naturally
- Combined with slanted band constraint (`band_width`), it limits drift while allowing flexible alignment
- Skipped reference words (words the LLM added/hallucinated) don't distort the alignment
- Vertical jump detection works cleanly: jumps represent real content gaps, not forced many-to-one artifacts

Trade-off: reference words that no prefix word maps to are invisible in the alignment. Handled by replacement logic (see `dtw_replacements.md`).

## Asymmetric Patterns

| Pattern | Description |
|---|---|
| `asymmetric` | Basic. Reference advances freely. No skip penalty. **Default.** |
| `asymmetricP0` | Rabiner-style, no slope penalty |
| `asymmetricP05` | Moderate slope penalty — discourages deviation from diagonal |
| `asymmetricP1` | Stronger slope penalty |
| `asymmetricP2` | Strongest — heavily penalizes deviation, similar to banding via cost |

Higher P values force alignment closer to diagonal. With slanted band already constraining drift, P variants add redundant control.

## Symmetric Patterns

Weight both directions equally. Every word participates — no free skips.

| Pattern | Description |
|---|---|
| `symmetric1` | Strictly diagonal (1,1) only. No warping. Sequences must be similar length. |
| `symmetric2` | Classic DTW. Allows (1,1), (1,0), (0,1). Most common symmetric pattern. |
| `symmetricP0`-`P2` | Slope-penalized variants |

For different-length files, symmetric patterns force many-to-one mappings creating noise in vertical jump detection.

## Special Patterns

| Pattern | Description |
|---|---|
| `rigid` | Only diagonal steps. Equivalent to `symmetric1`. |
| `mori2006` | Asymmetric pattern for speech recognition with varying tempo. |

## Rabiner & Juang Types (I-IV)

Academic/legacy patterns from speech recognition literature. Unlikely to offer advantages over `asymmetric` with banding for Hebrew transcription.

| Type | Slope range |
|---|---|
| Type I | 0 to infinity (most flexible) |
| Type II | 0.5 to 2 |
| Type III | 0.67 to 1.5 |
| Type IV | 1 to 1 (strictest) |

## Configuration

| Parameter | Env var | Default |
|-----------|---------|---------|
| `step_pattern` | `DTW_STEP_PATTERN` | `asymmetric` |
| `band_width` | `DTW_BAND_WIDTH` | 200 |
