"""Audio-text alignment service using stable-whisper."""

import logging
from pathlib import Path

import stable_whisper

logger = logging.getLogger(__name__)

_model = None


def load_model(model_name: str, device: str) -> None:
    """
    Load the stable-whisper model.

    Args:
        model_name: Model name (e.g., 'base', 'small', 'medium', 'large').
        device: Device to use ('cuda' or 'cpu').
    """
    global _model
    logger.info("Loading stable-whisper model: %s on %s", model_name, device)
    _model = stable_whisper.load_model(model_name, device=device)
    logger.info("Model loaded successfully")


def align_audio(audio_file: str, text: str, language: str):
    """
    Align audio with text using stable-whisper.

    Args:
        audio_file: Path to the audio file.
        text: Text content to align with audio.
        language: Language code (e.g., 'he' for Hebrew).

    Returns:
        WhisperResult object with alignment data.

    Raises:
        RuntimeError: If model not loaded.
    """
    if _model is None:
        raise RuntimeError("Model not loaded. Call load_model() first.")

    logger.info("Aligning audio: %s (%d characters)", audio_file, len(text))
    result = _model.align(audio_file, text, language=language)
    logger.info("Alignment completed")
    return result


def save_outputs(result, output_dir: Path, stem: str) -> tuple[Path, Path]:
    """
    Save alignment results as JSON and VTT files.

    Args:
        result: WhisperResult from alignment.
        output_dir: Directory to save output files.
        stem: Base filename (without extension).

    Returns:
        Tuple of (json_path, vtt_path).
    """
    json_path = output_dir / f"{stem}.json"
    vtt_path = output_dir / f"{stem}.vtt"

    logger.info("Saving JSON to: %s", json_path)
    result.save_as_json(str(json_path))

    logger.info("Saving VTT to: %s", vtt_path)
    result.to_srt_vtt(str(vtt_path), word_level=False)

    return json_path, vtt_path
