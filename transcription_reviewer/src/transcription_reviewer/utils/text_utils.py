"""Text utility functions for transcription processing."""


def word_count_diff(text: str, original_word_count: int) -> int:
    """Return absolute difference between word count of text and original."""
    return abs(len(text.split()) - original_word_count)


def split_by_words(content: str, max_words: int = 5000) -> list[str]:
    """Split content into chunks of ~max_words, breaking at line boundaries."""
    lines = content.strip().split("\n")
    if not lines:
        return [content]

    total_words = len(content.split())
    if total_words <= max_words:
        return [content.strip()]

    chunks = []
    current_lines: list[str] = []
    current_word_count = 0

    for line in lines:
        line_words = len(line.split())
        if current_word_count + line_words > max_words and current_lines:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_word_count = line_words
        else:
            current_lines.append(line)
            current_word_count += line_words

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks
