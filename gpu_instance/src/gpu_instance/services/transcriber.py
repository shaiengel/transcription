"""Audio transcription using faster-whisper."""

import logging
from typing import Any

from faster_whisper import WhisperModel

from ..config import config

logger = logging.getLogger(__name__)

# Global model instance (loaded once at startup)
_model: WhisperModel = None


def load_model() -> WhisperModel:
    """
    Load the Whisper model.

    The model is loaded once and reused for all transcriptions.

    Returns:
        Loaded WhisperModel instance.
    """
    global _model

    if _model is not None:
        return _model

    logger.info(f"Loading model: {config.model_name}")
    logger.info(f"Device: {config.device}, Compute type: {config.compute_type}")

    _model = WhisperModel(
        config.model_name,
        device=config.device,
        compute_type=config.compute_type,
    )

    logger.info("Model loaded successfully")
    return _model


def transcribe(audio_path: str) -> tuple[Any, Any]:
    """
    Transcribe an audio file.

    Args:
        audio_path: Path to the audio file.

    Returns:
        Tuple of (segments iterator, transcription info).
    """
    model = load_model()

    logger.info(f"Transcribing: {audio_path}")
    logger.info(f"Language: {config.language}, Beam size: {config.beam_size}")

    segments, info = model.transcribe(
        audio_path,
        language=config.language,
        beam_size=config.beam_size,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
        ),
    )

    logger.info(f"Detected language: {info.language} (probability: {info.language_probability:.2f})")
    logger.info(f"Audio duration: {info.duration:.2f}s")

    return segments, info
