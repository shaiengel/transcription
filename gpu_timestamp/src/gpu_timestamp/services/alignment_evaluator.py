"""Alignment quality evaluation service for detecting degradation in transcriptions.

Combines two evaluation approaches:
1. Pre-alignment DTW: Compares pre-fix .time with corrected .txt to fix hallucinations
   and find a cutoff point.
2. Post-alignment probability: Uses word probabilities from stable-whisper alignment
   to detect degradation via rolling average and CUSUM.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from dtw import dtw

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Segment:
    id: int
    timestamp: str
    text: str
    words: list[str]


@dataclass
class DTWAlignmentResult:
    segment_id: int
    original_text: str
    matched_text: str
    start_pos: int
    end_pos: int
    match_score: float
    insertions: list[str]
    deletions: list[str]


# =============================================================================
# TEXT PROCESSING (ported from banded_dtw.py, adapted for string input)
# =============================================================================

def _tokenize(text: str) -> list[str]:
    text = re.sub(r'[^\w\s\u0590-\u05FF]', ' ', text)
    return [w for w in text.split() if w.strip()]


def _parse_prefix_content(content: str) -> list[Segment]:
    """Parse .time format content from string."""
    segments = []
    pattern = r'\[(\d+)\]\s*([\d:.]+ - [\d:.]+):\s*(.+)'
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(pattern, line)
        if match:
            seg_id = int(match.group(1))
            timestamp = match.group(2)
            text = match.group(3)
            words = _tokenize(text)
            segments.append(Segment(id=seg_id, timestamp=timestamp, text=text, words=words))
    return segments


def _tokenize_corrected(text: str) -> list[str]:
    """Tokenize corrected text content."""
    text = text.replace('\n', ' ')
    return _tokenize(text)


# =============================================================================
# DISTANCE FUNCTIONS
# =============================================================================

def _levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            ins = prev_row[j + 1] + 1
            dels = curr_row[j] + 1
            subs = prev_row[j] + (c1 != c2)
            curr_row.append(min(ins, dels, subs))
        prev_row = curr_row
    return prev_row[-1]


def _word_distance(w1: str, w2: str) -> float:
    if w1 == w2:
        return 0.0
    edit_dist = _levenshtein_distance(w1, w2)
    max_len = max(len(w1), len(w2))
    return edit_dist / max_len if max_len > 0 else 0.0


# =============================================================================
# BANDED DTW ALIGNMENT
# =============================================================================

def _auto_band_width(n: int, m: int, window_type: str) -> int:
    """Auto-calculate band width based on word count difference and window type."""
    diff = abs(n - m)
    if window_type == 'sakoechiba':
        bw = max(diff + 50, 200)
    else:
        bw = max(diff // 2 + 50, 200)
    logger.info("Auto-calculated band width: %d (%s, word count diff=%d)", bw, window_type, diff)
    return bw


def _banded_dtw_alignment(
    all_prefix_words: list[str],
    corrected_words: list[str],
    band_width: int | None = None,
    step_pattern: str = 'asymmetric',
    window_type: str = 'slantedband',
):
    n, m = len(all_prefix_words), len(corrected_words)
    logger.info("Computing distance matrix (%d x %d)...", n, m)

    dist_matrix = np.zeros((n, m), dtype=np.float64)
    for i, w1 in enumerate(all_prefix_words):
        if i % 500 == 0:
            logger.info("    Processing row %d/%d...", i, n)
        for j, w2 in enumerate(corrected_words):
            dist_matrix[i, j] = _word_distance(w1, w2)

    if window_type == 'none':
        logger.info("Running DTW without window constraint...")
        alignment = dtw(
            dist_matrix,
            step_pattern=step_pattern,
            keep_internals=True,
        )
    else:
        if band_width is None or band_width == 0:
            band_width = _auto_band_width(n, m, window_type)
        logger.info("Running DTW with %s constraint (width=%d)...", window_type, band_width)
        alignment = dtw(
            dist_matrix,
            step_pattern=step_pattern,
            keep_internals=True,
            window_type=window_type,
            window_args={'window_size': band_width},
        )

    return list(zip(alignment.index1, alignment.index2)), alignment, dist_matrix


def _map_segments_from_global(
    segments: list[Segment],
    all_prefix_words: list[str],
    corrected_words: list[str],
    alignment_path: list[tuple[int, int]],
    match_threshold: float = 0.5,
) -> list[DTWAlignmentResult]:
    results = []

    prefix_to_corrected: dict[int, list[int]] = {}
    for p_idx, c_idx in alignment_path:
        if p_idx not in prefix_to_corrected:
            prefix_to_corrected[p_idx] = []
        prefix_to_corrected[p_idx].append(c_idx)

    word_idx = 0
    for seg in segments:
        seg_start_idx = word_idx
        seg_end_idx = word_idx + len(seg.words) - 1

        corrected_positions = []
        for i in range(seg_start_idx, seg_end_idx + 1):
            if i in prefix_to_corrected:
                corrected_positions.extend(prefix_to_corrected[i])

        if corrected_positions:
            matched_start = min(corrected_positions)
            matched_end = max(corrected_positions) + 1
        else:
            matched_start = matched_end = 0

        matches = 0
        insertions = []
        deletions = []
        matched_corrected = set(corrected_positions)

        for i, word in enumerate(seg.words):
            prefix_idx = seg_start_idx + i
            if prefix_idx in prefix_to_corrected:
                good_match = False
                for c_idx in prefix_to_corrected[prefix_idx]:
                    if c_idx < len(corrected_words):
                        dist = _word_distance(word, corrected_words[c_idx])
                        if dist <= match_threshold:
                            good_match = True
                            break
                if good_match:
                    matches += 1
                else:
                    deletions.append(word)
            else:
                deletions.append(word)

        if corrected_positions:
            for c_idx in range(matched_start, matched_end):
                if c_idx not in matched_corrected:
                    insertions.append(corrected_words[c_idx])

        match_score = matches / len(seg.words) if seg.words else 0.0
        matched_text = ' '.join(corrected_words[matched_start:matched_end]) if corrected_positions else ""

        results.append(DTWAlignmentResult(
            segment_id=seg.id,
            original_text=seg.text,
            matched_text=matched_text,
            start_pos=matched_start,
            end_pos=matched_end,
            match_score=match_score,
            insertions=insertions,
            deletions=deletions,
        ))

        word_idx += len(seg.words)

    return results


# =============================================================================
# VERTICAL JUMP DETECTION
# =============================================================================

def _detect_vertical_jumps(alignment, min_jump_size: int = 5) -> list[dict]:
    idx1 = np.array(alignment.index1)
    idx2 = np.array(alignment.index2)

    diff_idx1 = np.diff(idx1)
    diff_idx2 = np.diff(idx2)

    vertical_mask = (diff_idx2 == 0) & (diff_idx1 > 0)
    vertical_positions = np.where(vertical_mask)[0]

    if len(vertical_positions) == 0:
        return []

    jumps_raw = []
    start = vertical_positions[0]
    for i in range(1, len(vertical_positions)):
        if vertical_positions[i] != vertical_positions[i - 1] + 1:
            jumps_raw.append((start, vertical_positions[i - 1]))
            start = vertical_positions[i]
    jumps_raw.append((start, vertical_positions[-1]))

    jumps = []
    for s, e in jumps_raw:
        prefix_start = idx1[s]
        prefix_end = idx1[e + 1] if e + 1 < len(idx1) else idx1[e]
        jump_size = prefix_end - prefix_start
        if jump_size >= min_jump_size:
            jumps.append({
                'corrected_idx': int(idx2[s]),
                'prefix_start': int(prefix_start),
                'prefix_end': int(prefix_end),
                'jump_size': int(jump_size),
            })

    jumps.sort(key=lambda x: x['jump_size'], reverse=True)

    if jumps:
        logger.info("Detected %d vertical jumps (>= %d words)", len(jumps), min_jump_size)
        for i, j in enumerate(jumps[:10]):
            logger.info(
                "  %d. Corrected idx %d: prefix %d-%d (%d words)",
                i + 1, j['corrected_idx'], j['prefix_start'], j['prefix_end'], j['jump_size'],
            )

    return jumps


# =============================================================================
# DROP DETECTION
# =============================================================================

def _detect_drop(
    results: list[DTWAlignmentResult],
    threshold: float = 0.25,
    word_count_threshold: int = 15,
    ma_window: int = 10,
) -> dict:
    match_scores = [r.match_score for r in results]
    word_counts = [len(_tokenize(r.original_text)) for r in results]
    n = len(match_scores)

    moving_avg = []
    for i in range(n):
        start = max(0, i - ma_window + 1)
        window = match_scores[start : i + 1]
        moving_avg.append(sum(window) / len(window))

    first_ma_drop = None
    ma_crossed_at = None
    for i, ma in enumerate(moving_avg):
        if ma < threshold:
            ma_crossed_at = i
            first_ma_drop = max(0, i - ma_window + 1)
            break

    first_words_drop = None
    cumulative_words = 0
    start_idx = None

    for i, (score, wc) in enumerate(zip(match_scores, word_counts)):
        if score < threshold:
            if start_idx is None:
                start_idx = i
                cumulative_words = 0
            cumulative_words += wc
            if cumulative_words >= word_count_threshold and first_words_drop is None:
                first_words_drop = start_idx
        else:
            start_idx = None
            cumulative_words = 0

    first_drop_idx = None
    first_drop_type = None

    if first_words_drop is not None and first_ma_drop is not None:
        if first_words_drop <= first_ma_drop:
            first_drop_idx = first_words_drop
            first_drop_type = 'words'
        else:
            first_drop_idx = first_ma_drop
            first_drop_type = 'moving_avg'
    elif first_words_drop is not None:
        first_drop_idx = first_words_drop
        first_drop_type = 'words'
    elif first_ma_drop is not None:
        first_drop_idx = first_ma_drop
        first_drop_type = 'moving_avg'

    if first_drop_idx is not None:
        logger.info("DTW drop at segment index %d (type: %s)", first_drop_idx, first_drop_type)
    else:
        logger.info("No DTW drop detected")

    return {
        'moving_avg': moving_avg,
        'first_drop_idx': first_drop_idx,
        'first_drop_type': first_drop_type,
        'first_ma_drop': first_ma_drop,
        'ma_crossed_at': ma_crossed_at,
        'first_words_drop': first_words_drop,
        'word_counts': word_counts,
    }


# =============================================================================
# CUTOFF AND REPLACEMENTS
# =============================================================================

def _find_cutoff_index(
    vertical_jumps: list[dict],
    drop_info: dict,
    results: list[DTWAlignmentResult],
    jump_threshold: int = 40,
) -> int | None:
    large_jumps = [j for j in vertical_jumps if j['jump_size'] > jump_threshold]
    jump_cutoff = None
    if large_jumps:
        large_jumps.sort(key=lambda x: x['corrected_idx'])
        jump_cutoff = large_jumps[0]['corrected_idx']
        logger.info(
            "First vertical jump > %d words at corrected index %d (%d words)",
            jump_threshold, jump_cutoff, large_jumps[0]['jump_size'],
        )

    drop_cutoff = None
    if drop_info and drop_info['first_drop_idx'] is not None:
        drop_idx = drop_info['first_drop_idx']
        if drop_idx < len(results):
            drop_cutoff = results[drop_idx].start_pos
            logger.info("First drop at corrected index %d (segment index %d)", drop_cutoff, drop_idx)

    candidates = [c for c in [jump_cutoff, drop_cutoff] if c is not None]
    if not candidates:
        logger.info("No DTW cutoff point found")
        return None

    cutoff = min(candidates)
    source = 'vertical_jump' if (jump_cutoff is not None and cutoff == jump_cutoff) else 'drop'
    logger.info("DTW cutoff index: %d (source: %s)", cutoff, source)
    return cutoff


def _identify_replacements(
    results: list[DTWAlignmentResult],
    segments: list[Segment],
    all_prefix_words: list[str],
    corrected_words: list[str],
    alignment_path: list[tuple[int, int]],
    cutoff_index: int,
    low_score_threshold: float = 0.5,
    high_dist_threshold: float = 0.7,
    match_threshold: float = 0.5,
) -> list[dict]:
    prefix_to_corrected: dict[int, list[int]] = {}
    corrected_to_prefix: dict[int, list[int]] = {}
    for p_idx, c_idx in alignment_path:
        if p_idx not in prefix_to_corrected:
            prefix_to_corrected[p_idx] = []
        prefix_to_corrected[p_idx].append(c_idx)
        if c_idx not in corrected_to_prefix:
            corrected_to_prefix[c_idx] = []
        corrected_to_prefix[c_idx].append(p_idx)

    def is_anchored_externally(c_idx, seg_start, seg_end):
        for p_idx in corrected_to_prefix.get(c_idx, []):
            if p_idx < seg_start or p_idx > seg_end:
                dist = _word_distance(all_prefix_words[p_idx], corrected_words[c_idx])
                if dist <= match_threshold:
                    return True, p_idx
        return False, None

    replacements = []
    word_idx = 0
    for seg, result in zip(segments, results):
        seg_start_idx = word_idx
        seg_end_idx = word_idx + len(seg.words) - 1
        word_idx += len(seg.words)

        if result.match_score >= low_score_threshold:
            continue
        if result.end_pos > cutoff_index:
            continue

        word_info = []
        for i, word in enumerate(seg.words):
            prefix_idx = seg_start_idx + i
            if prefix_idx in prefix_to_corrected:
                c_idx = int(prefix_to_corrected[prefix_idx][0])
                dist = _word_distance(word, corrected_words[c_idx]) if c_idx < len(corrected_words) else 1.0
                word_info.append((prefix_idx, word, c_idx, dist))
            else:
                word_info.append((prefix_idx, word, None, 1.0))

        corrected_indices = set(w[2] for w in word_info if w[2] is not None)
        if len(corrected_indices) == 1:
            c_idx = corrected_indices.pop()
            anchored, anchor_p_idx = is_anchored_externally(c_idx, seg_start_idx, seg_end_idx)
            if anchored:
                mode = 'insert_before' if anchor_p_idx > seg_end_idx else 'insert_after'
                replacements.append({
                    'corrected_indices': [c_idx],
                    'prefix_words': [w[1] for w in word_info],
                    'mode': mode,
                })
            else:
                replacements.append({
                    'corrected_indices': [c_idx],
                    'prefix_words': [w[1] for w in word_info],
                    'mode': 'replace',
                })
            continue

        groups = []
        current_group = []
        current_c_idx = None
        for info in word_info:
            p_idx, p_word, c_idx, dist = info
            if dist > high_dist_threshold and c_idx is not None and (current_c_idx is None or c_idx == current_c_idx):
                current_group.append(info)
                current_c_idx = c_idx
            else:
                if len(current_group) >= 2:
                    groups.append(current_group)
                current_group = []
                current_c_idx = None
        if len(current_group) >= 2:
            groups.append(current_group)

        for group in groups:
            c_idx = group[0][2]
            all_for_c_idx = [w[1] for w in word_info if w[2] == c_idx]
            anchored, anchor_p_idx = is_anchored_externally(c_idx, seg_start_idx, seg_end_idx)
            if anchored:
                mode = 'insert_before' if anchor_p_idx > seg_end_idx else 'insert_after'
                replacements.append({
                    'corrected_indices': [c_idx],
                    'prefix_words': all_for_c_idx,
                    'mode': mode,
                })
            else:
                replacements.append({
                    'corrected_indices': [c_idx],
                    'prefix_words': all_for_c_idx,
                    'mode': 'replace',
                })

    replacements.sort(key=lambda r: min(r['corrected_indices']))
    merged = []
    for rep in replacements:
        if (merged
                and min(rep['corrected_indices']) <= max(merged[-1]['corrected_indices'])
                and rep.get('mode', 'replace') == merged[-1].get('mode', 'replace')):
            prev = merged[-1]
            prev['corrected_indices'] = sorted(set(prev['corrected_indices'] + rep['corrected_indices']))
            prev['prefix_words'].extend(rep['prefix_words'])
        else:
            merged.append(rep)

    if merged:
        logger.info("Identified %d replacements for low-score segments before cutoff", len(merged))

    return merged


def _build_word_char_spans(text: str) -> list[tuple[int, int]]:
    cleaned = re.sub(r'[^\w\s\u0590-\u05FF]', ' ', text)
    spans = []
    i = 0
    while i < len(cleaned):
        while i < len(cleaned) and cleaned[i].isspace():
            i += 1
        if i >= len(cleaned):
            break
        start = i
        while i < len(cleaned) and not cleaned[i].isspace():
            i += 1
        spans.append((start, i))
    return spans


def _apply_replacements(original_text: str, replacements: list[dict]) -> str:
    """Apply replacements to text without truncating."""
    if not replacements:
        return original_text

    word_spans = _build_word_char_spans(original_text)

    for rep in reversed(replacements):
        first_idx = min(rep['corrected_indices'])
        last_idx = max(rep['corrected_indices'])
        if first_idx < len(word_spans) and last_idx < len(word_spans):
            char_start = word_spans[first_idx][0]
            char_end = word_spans[last_idx][1]
            mode = rep.get('mode', 'replace')
            original_segment = original_text[char_start:char_end]
            insert_text = ' '.join(rep['prefix_words'])
            logger.info(
                "Replacement [%s] at indices %d-%d: '%s' -> '%s'",
                mode, first_idx, last_idx, original_segment, insert_text,
            )
            if mode == 'insert_before':
                original_text = original_text[:char_start] + insert_text + ' ' + original_text[char_start:]
            elif mode == 'insert_after':
                original_text = original_text[:char_end] + ' ' + insert_text + original_text[char_end:]
            else:
                original_text = original_text[:char_start] + insert_text + original_text[char_end:]

    return original_text


# =============================================================================
# PROBABILITY-BASED EVALUATION (existing logic)
# =============================================================================

def extract_words_from_stable_whisper(json_path: Path) -> list[dict]:
    """Extract word data from stable_whisper JSON output."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    words = []
    for segment in data.get("segments", []):
        for word_data in segment.get("words", []):
            words.append({
                "word": word_data.get("word", "").strip(),
                "start_time": word_data.get("start"),
                "end_time": word_data.get("end"),
                "probability": word_data.get("probability"),
            })

    return words


def _compute_moving_average(probs: np.ndarray, window: int = 100) -> np.ndarray:
    """Compute moving average with given window size."""
    moving_avg = np.full(len(probs), np.nan)
    for i in range(len(probs)):
        start = max(0, i - window + 1)
        moving_avg[i] = np.mean(probs[start : i + 1])
    return moving_avg


def detect_degradation_rolling_avg(
    probs: np.ndarray,
    window: int = 100,
    target: float = 0.25,
) -> int:
    """Find index where rolling avg drops below target."""
    if len(probs) < window * 3:
        logger.debug("Not enough data points for rolling avg analysis")
        return -1

    moving_avg = _compute_moving_average(probs, window)

    for i in range(window, len(moving_avg)):
        if moving_avg[i] < target:
            return i
    return -1


def detect_degradation_cusum(
    probs: np.ndarray,
    window: int = 100,
    threshold: float = 50.0,
) -> int:
    """CUSUM change point detection for detecting mean shift."""
    if len(probs) < 100:
        logger.debug("Not enough data points for CUSUM analysis")
        return -1

    moving_avg = _compute_moving_average(probs, window)
    target = 0.25

    cusum_neg = 0

    for i in range(window, len(moving_avg)):
        diff = moving_avg[i] - target
        cusum_neg = min(0, cusum_neg + diff)

        if abs(cusum_neg) > threshold:
            return i
    return -1


# =============================================================================
# VTT / SRT TRUNCATION (existing logic)
# =============================================================================

def truncate_vtt_file(file_path: Path, word_index: int) -> None:
    """Truncate VTT file at the segment containing word_index."""
    content = file_path.read_text(encoding="utf-8")
    parts = content.strip().split("\n\n")

    output_parts = [parts[0]]
    cumulative_words = 0

    for segment in parts[1:]:
        lines = segment.split("\n")
        text = " ".join(lines[1:]) if len(lines) > 1 else ""
        words_in_segment = len(text.split())
        if cumulative_words + words_in_segment >= word_index:
            break
        output_parts.append(segment)
        cumulative_words += words_in_segment

    file_path.write_text("\n\n".join(output_parts) + "\n", encoding="utf-8")
    logger.info("Truncated VTT at word %d (kept %d words)", word_index, cumulative_words)


def truncate_srt_file(file_path: Path, word_index: int) -> None:
    """Truncate SRT file at the segment containing word_index."""
    content = file_path.read_text(encoding="utf-8")
    segments = content.strip().split("\n\n")

    output_segments = []
    cumulative_words = 0

    for segment in segments:
        lines = segment.split("\n")
        text = " ".join(lines[2:]) if len(lines) > 2 else ""
        words_in_segment = len(text.split())
        if cumulative_words + words_in_segment >= word_index:
            break
        output_segments.append(segment)
        cumulative_words += words_in_segment

    file_path.write_text("\n\n".join(output_segments) + "\n", encoding="utf-8")
    logger.info("Truncated SRT at word %d (kept %d words)", word_index, cumulative_words)


# =============================================================================
# ALIGNMENT EVALUATOR CLASS
# =============================================================================

class AlignmentEvaluator:
    """Holds evaluation state across pre-alignment DTW and post-alignment probability phases."""

    def __init__(
        self,
        band_width: int | None = None,
        step_pattern: str = "asymmetric",
        window_type: str = "slantedband",
        match_threshold: float = 0.5,
        high_dist_threshold: float = 0.7,
        low_score_threshold: float = 0.5,
        jump_threshold: int = 40,
        drop_threshold: float = 0.25,
        ma_window: int = 10,
        rolling_avg_target: float = 0.25,
    ):
        self.band_width = band_width
        self.step_pattern = step_pattern
        self.window_type = window_type
        self.match_threshold = match_threshold
        self.high_dist_threshold = high_dist_threshold
        self.low_score_threshold = low_score_threshold
        self.jump_threshold = jump_threshold
        self.drop_threshold = drop_threshold
        self.ma_window = ma_window
        self.rolling_avg_target = rolling_avg_target

        self.dtw_cutoff_index: int | None = None
        self.dtw_replacements: list[dict] = []
        self.rolling_avg_index: int = -1
        self.cusum_index: int = -1
        self.fixed_text: str | None = None

    def _compute_truncate_point(self) -> int:
        """Determine truncate point from DTW cutoff and rolling avg indices.

        Prefers dtw_cutoff_index when available, falls back to rolling_avg_index.
        """
        if self.dtw_cutoff_index is not None:
            return self.dtw_cutoff_index
        return self.rolling_avg_index

    def pre_alignment_fix(self, prefix_time_content: str, corrected_text: str) -> str:
        """Run DTW between pre-fix .time content and corrected text.

        Applies fixes (replacements) to undo LLM hallucinations but does NOT truncate.
        Stores dtw_cutoff_index for later use.

        Args:
            prefix_time_content: Content of the .pre-fix.time file.
            corrected_text: Content of the corrected .txt file.

        Returns:
            Fixed text with hallucinations corrected.
        """
        segments = _parse_prefix_content(prefix_time_content)
        corrected_words = _tokenize_corrected(corrected_text)

        if not segments or not corrected_words:
            logger.warning("Empty segments or corrected words, skipping DTW fix")
            self.fixed_text = corrected_text
            return corrected_text

        all_prefix_words = []
        for seg in segments:
            all_prefix_words.extend(seg.words)

        logger.info(
            "DTW pre-alignment: %d prefix words, %d corrected words",
            len(all_prefix_words), len(corrected_words),
        )

        alignment_path, alignment_obj, _dist_matrix = _banded_dtw_alignment(
            all_prefix_words, corrected_words,
            band_width=self.band_width,
            step_pattern=self.step_pattern,
            window_type=self.window_type,
        )

        vertical_jumps = _detect_vertical_jumps(alignment_obj)

        results = _map_segments_from_global(
            segments, all_prefix_words, corrected_words, alignment_path,
            match_threshold=self.match_threshold,
        )

        drop_info = _detect_drop(
            results, threshold=self.drop_threshold, ma_window=self.ma_window,
        )

        self.dtw_cutoff_index = _find_cutoff_index(
            vertical_jumps, drop_info, results, jump_threshold=self.jump_threshold,
        )

        if self.dtw_cutoff_index is not None:
            self.dtw_replacements = _identify_replacements(
                results, segments, all_prefix_words, corrected_words,
                alignment_path, self.dtw_cutoff_index,
                low_score_threshold=self.low_score_threshold,
                high_dist_threshold=self.high_dist_threshold,
                match_threshold=self.match_threshold,
            )
            self.fixed_text = _apply_replacements(corrected_text, self.dtw_replacements)
            logger.info(
                "Applied %d DTW replacements, cutoff at word %d",
                len(self.dtw_replacements), self.dtw_cutoff_index,
            )
        else:
            self.fixed_text = corrected_text
            logger.info("No DTW cutoff detected, text unchanged")

        return self.fixed_text

    def post_alignment_evaluate(self, json_path: Path) -> dict | None:
        """Evaluate alignment quality using word probabilities.

        Runs rolling average and CUSUM on stable-whisper probabilities.
        CUSUM is computed for informational purposes but not used in truncation decisions.

        Args:
            json_path: Path to the stable_whisper JSON file.

        Returns:
            Dictionary with analysis results if degradation detected, None otherwise.
        """
        try:
            words = extract_words_from_stable_whisper(json_path)

            if not words:
                logger.warning("No words found in JSON file: %s", json_path)
                return None

            words.sort(key=lambda w: w["start_time"] if w["start_time"] is not None else 0)

            probs = [w["probability"] for w in words if w["probability"] is not None]

            if len(probs) < 100:
                logger.debug("Not enough probability data: %d words", len(probs))
                return None

            probs_array = np.array(probs)

            self.rolling_avg_index = detect_degradation_rolling_avg(probs_array, target=self.rolling_avg_target)
            self.cusum_index = detect_degradation_cusum(probs_array)

            logger.info(
                "Post-alignment: rolling_avg=%d, cusum=%d, dtw_cutoff=%s",
                self.rolling_avg_index, self.cusum_index, self.dtw_cutoff_index,
            )

            # Truncation decision: either rolling_avg or dtw detects issues
            should_truncate = (
                self.rolling_avg_index != -1 or self.dtw_cutoff_index is not None
            )

            # Always return analysis dict with all methods
            analysis = {
                "rolling_avg_method": self.rolling_avg_index,
                "cusum_method": self.cusum_index,
                "dtw_cutoff_index": self.dtw_cutoff_index,
                "dtw_replacements_count": len(self.dtw_replacements),
                "should_truncate": should_truncate,
            }

            if should_truncate:
                analysis["truncate_point"] = self._compute_truncate_point()

            return analysis

        except Exception as e:
            logger.error(
                "Error evaluating alignment for %s: %s", json_path, e, exc_info=True,
            )
            return None
