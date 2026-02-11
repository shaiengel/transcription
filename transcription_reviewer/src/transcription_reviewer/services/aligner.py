"""Audio-text alignment service using stable-whisper."""

import logging

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


def is_model_loaded() -> bool:
    """Check if the model is loaded."""
    return _model is not None


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


def get_vtt_content(result, vtt_path: str) -> str:
    """
    Get VTT content as string from alignment result.

    Args:
        result: WhisperResult from alignment.
        vtt_path: Path to save VTT file (must have .vtt extension).

    Returns:
        VTT content as string.
    """
    result.to_srt_vtt(vtt_path, word_level=False)
    with open(vtt_path, "r", encoding="utf-8") as f:
        return f.read()


def get_json_content(result) -> str:
    """
    Get JSON content as string from alignment result.

    Args:
        result: WhisperResult from alignment.

    Returns:
        JSON content as string.
    """
    import json

    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
