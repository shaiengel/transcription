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


def test_align_local_files(
    text_path: str = r"C:\Users\z0050yye\Downloads\151771.txt",
    audio_path: str = r"C:\Users\z0050yye\Downloads\151771.mp3",
    language: str = "he",
    model_name: str = "large",
    device: str = "cpu",
    token_step: int = 200,
) -> None:
    """
    Test alignment with local files.

    Args:
        text_path: Path to the text file.
        audio_path: Path to the audio file.
        language: Language code.
        model_name: Whisper model name.
        device: Device to use.
        token_step: Token step for alignment.
    """
    # Read text from file
    text = Path(text_path).read_text(encoding="utf-8")
    logger.info("Read %d characters from %s", len(text), text_path)

    # Load model if not already loaded
    if _model is None:
        load_model(model_name, device)

    # Run alignment
    result = align_audio(audio_path, text, language, token_step)

    # Save outputs to same directory as audio
    output_dir = Path(audio_path).parent
    stem = Path(audio_path).stem
    json_path, vtt_path = save_outputs(result, output_dir, stem)

    logger.info("Output saved: %s, %s", json_path, vtt_path)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_align_local_files()
