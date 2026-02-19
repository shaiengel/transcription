"""Audio-text alignment service using stable-whisper."""

import json
import logging
from pathlib import Path

import stable_whisper

from gpu_timestamp.services.alignment_evaluator import (
    evaluate_alignment,
    truncate_srt_file,
    truncate_vtt_file,
)

logger = logging.getLogger(__name__)

_model = None


def load_model(model_name: str, device: str, download_root: str | None = None) -> None:
    """
    Load the stable-whisper model.

    Args:
        model_name: Model name (e.g., 'base', 'small', 'medium', 'large').
        device: Device to use ('cuda' or 'cpu').
        download_root: Directory to look for/download models. If None, uses default cache.
    """
    global _model
    # Treat empty string as None (use default cache)
    download_root = download_root or None
    logger.info("Loading stable-whisper model: %s on %s (cache: %s)", model_name, device, download_root or "default")
    _model = stable_whisper.load_model(model_name, device=device, download_root=download_root)
    logger.info("Model loaded successfully")


def align_audio(audio_file: str, text: str, language: str, token_step: int) -> stable_whisper.WhisperResult:
    """
    Align audio with text using stable-whisper.

    Args:
        audio_file: Path to the audio file.
        text: Text content to align with audio.
        language: Language code (e.g., 'he' for Hebrew).
        token_step: Token step for alignment.

    Returns:
        WhisperResult object with alignment data.

    Raises:
        RuntimeError: If model not loaded.
    """
    if _model is None:
        raise RuntimeError("Model not loaded. Call load_model() first.")

    logger.info("Aligning audio: %s (%d characters)", audio_file, len(text))
    result = _model.align(audio_file, text, language=language, token_step=token_step)
    logger.info("Alignment completed")
    return result


def save_outputs(result, output_dir: Path, stem: str) -> tuple[Path, Path, Path]:
    """
    Save alignment results as JSON, VTT, and SRT files.

    Args:
        result: WhisperResult from alignment.
        output_dir: Directory to save output files.
        stem: Base filename (without extension).

    Returns:
        Tuple of (json_path, vtt_path, srt_path).
    """
    json_path = output_dir / f"{stem}.json"
    vtt_path = output_dir / f"{stem}.vtt"
    srt_path = output_dir / f"{stem}.srt"

    logger.info("Saving JSON to: %s", json_path)
    result.save_as_json(str(json_path))

    logger.info("Saving VTT to: %s", vtt_path)
    result.to_srt_vtt(str(vtt_path), word_level=False)

    logger.info("Saving SRT to: %s", srt_path)
    result.to_srt_vtt(str(srt_path), word_level=False)

    return json_path, vtt_path, srt_path


def test_align_local_files(
    text_path: str = r"C:\Users\z0050yye\Downloads\303549.txt",
    audio_path: str = r"C:\Users\z0050yye\Downloads\303549.mp3",
    json_path: str = r"C:\Users\z0050yye\Downloads\303549.json",
    vtt_path: str = r"C:\Users\z0050yye\Downloads\303549.vtt",
    language: str = "he",
    model_name: str = "base",
    device: str = "cpu",
    token_step: int = 100,
) -> None:
    """
    Test alignment with local files.

    Args:
        text_path: Path to the text file.
        audio_path: Path to the audio file.
        json_path: Path to existing JSON file for evaluation.
        vtt_path: Path to existing VTT file for evaluation.
        language: Language code.
        model_name: Whisper model name.
        device: Device to use.
        token_step: Token step for alignment.
    """
    json_file = Path(json_path)
    output_dir = json_file.parent
    stem = json_file.stem

    # Read JSON content
    # json_content = json_file.read_text(encoding="utf-8")
    # logger.info("Read %d characters from %s", len(json_content), json_path)
    # vtt_content = Path(vtt_path).read_text(encoding="utf-8")
    # logger.info("Read %d characters from %s", len(vtt_content), vtt_path)

    # Load model if not already loaded
    if _model is None:
        load_model(model_name, device)

    # Run alignment
    result = align_audio(audio_path, text_path, language, token_step)

    # Save outputs to same directory as audio
    json_path, vtt_path, srt_path = save_outputs(result, output_dir, stem)

    logger.info("Output saved: %s, %s, %s", json_path, vtt_path, srt_path)

    # Evaluate alignment quality
    analysis_result = evaluate_alignment(json_path)
    if analysis_result:
        logger.warning(
            "Degradation detected: rolling_avg=%d, cusum=%d",
            analysis_result["rolling_avg_method"],
            analysis_result["cusum_method"],
        )
        # Truncate VTT/SRT at degradation point
        truncate_vtt_file(Path(vtt_path), analysis_result["cusum_method"])
        truncate_srt_file(srt_path, analysis_result["cusum_method"])

        # Save analysis file
        analysis_path = output_dir / f"{stem}.analysis"
        analysis_path.write_text(
            json.dumps(analysis_result, indent=2),
            encoding="utf-8",
        )
        logger.info("Analysis saved: %s", analysis_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_align_local_files()
