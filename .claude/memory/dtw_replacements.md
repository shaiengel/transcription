# DTW Replacement Logic

Source: `alignment_evaluator.py` → `_identify_replacements()` and `_apply_replacements()`

## Overview

After banded DTW alignment, each prefix word maps to one or more corrected words. For low-score segments (< `low_score_threshold`, default 0.5) **before the cutoff index**, the code identifies corrected words that should be replaced with the original prefix words to restore content the LLM incorrectly changed. Segments whose `end_pos` is beyond the cutoff are skipped entirely.

## Grouping Rules

A **group** is a set of consecutive prefix words within a segment that:
1. All have **high distance** (> `high_dist_threshold`, default 0.7) to their mapped corrected word
2. All map to the **same corrected index**
3. Contains at least **2 words**

## Case A: All Words Map to Same Corrected Index

When every prefix word in a low-score segment maps to the same single corrected word, the replacement includes **all** prefix words in their original order.

## Case B: Consecutive High-Distance Groups

When only some prefix words form a group, the group triggers a replacement. The replacement includes **all prefix words mapping to the same corrected index** (not just the group members).

## External Anchoring Check

Before replacing a corrected word, check if it's **anchored externally** — a prefix word in a **different segment** maps to it with good distance (<= `match_threshold`, default 0.5).

If anchored, the corrected word is **preserved** and prefix words are **inserted** alongside it.

### Insert Position (Speech Order)

- Anchoring word in **later** segment → `insert_before`: group words inserted before corrected word
- Anchoring word in **earlier** segment → `insert_after`: corrected word stays first

### Not Anchored

Corrected word is **replaced** entirely with prefix words.

## Replacement Modes Summary

| Mode | Corrected word | Prefix words | When |
|------|---------------|-------------|------|
| `replace` | Removed | Substituted in its place | No external anchor |
| `insert_before` | Preserved | Inserted before it | Anchored by later segment |
| `insert_after` | Preserved | Inserted after it | Anchored by earlier segment |

## Merging

Replacements targeting overlapping corrected indices are merged, but only if they share the same mode.

## Configurable Parameters

| Parameter | Env var | Default | Purpose |
|-----------|---------|---------|---------|
| `low_score_threshold` | `DTW_LOW_SCORE_THRESHOLD` | 0.5 | Segment score below which replacements are considered |
| `high_dist_threshold` | `DTW_HIGH_DIST_THRESHOLD` | 0.7 | Word distance above which words are grouped for replacement |
| `match_threshold` | `DTW_MATCH_THRESHOLD` | 0.5 | Distance threshold for anchoring check |
