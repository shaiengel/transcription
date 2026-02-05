"""Service to fetch Steinsaltz commentary from GitLab Sefaria pages."""

import json
import logging
import re

from audio_manager.infrastructure.gitlab_client import GitLabClient

logger = logging.getLogger(__name__)

SEFARIA_BASE_PATH = "backend/data/sefaria_pages"

# Regex to match HTML tags
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def strip_html_tags(text: str) -> str:
    """Remove all HTML tags, newlines, and unescape characters from text."""
    cleaned = HTML_TAG_PATTERN.sub("", text)
    # Remove newlines and collapse multiple spaces
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    # Remove all backslashes (they're escape characters that shouldn't appear in final text)
    cleaned = cleaned.replace("\\", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def get_daf_pages(daf_id: int) -> list[str]:
    """Convert daf_id to page suffixes (a and/or b).

    In Talmud, each daf has two sides: amud aleph (a) and amud bet (b).
    daf_id typically represents the daf number.

    Returns list like ['2a', '2b'] for daf_id 2.
    """
    return [f"{daf_id}a", f"{daf_id}b"]


def extract_steinsaltz_commentary(json_content: str) -> str | None:
    """Extract the Steinsaltz Hebrew commentary from a Sefaria page JSON.

    Args:
        json_content: The raw JSON string from the Sefaria page file.

    Returns:
        The Steinsaltz Hebrew commentary text (HTML tags removed), or None if not found.
    """
    try:
        data = json.loads(json_content)

        # Navigate to commentaries.שטיינזלץ.he
        commentaries = data.get("commentaries", {})
        steinsaltz = commentaries.get("שטיינזלץ", {})
        hebrew_text = steinsaltz.get("he")

        if hebrew_text:
            # hebrew_text could be a list or a string
            if isinstance(hebrew_text, list):
                # Join list items, handling nested lists
                raw_text = _flatten_and_join(hebrew_text)
            else:
                raw_text = str(hebrew_text)

            # Remove HTML tags
            return strip_html_tags(raw_text)

        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", e)
        return None
    except Exception as e:
        logger.error("Failed to extract Steinsaltz commentary: %s", e)
        return None


def _flatten_and_join(items: list, separator: str = " ") -> str:
    """Flatten nested lists and join with separator."""
    result = []
    for item in items:
        if isinstance(item, list):
            result.append(_flatten_and_join(item, separator))
        elif item:
            result.append(str(item))
    return separator.join(result)


def fetch_steinsaltz_for_daf(
    gitlab_client: GitLabClient,
    massechet_name: str,
    daf_id: int,
    branch: str = "main",
) -> str | None:
    """Fetch Steinsaltz commentary for a specific daf from GitLab.

    Fetches both the 'a' and 'b' pages if they exist and combines them.

    Args:
        gitlab_client: The GitLab client instance.
        massechet_name: The Sefaria folder name for the massechet.
        daf_id: The daf number.
        branch: Git branch to fetch from.

    Returns:
        Combined Steinsaltz commentary from both pages, or None if not found.
    """
    pages = get_daf_pages(daf_id)
    commentaries = []

    for page in pages:
        # Build path like: backend/data/sefaria_pages/arakhin/arakhin_10a.json
        file_path = f"{SEFARIA_BASE_PATH}/{massechet_name}/{massechet_name}_{page}.json"
        logger.info("Fetching Sefaria page: %s", file_path)

        content = gitlab_client.get_file_content(file_path, branch)
        if content:
            commentary = extract_steinsaltz_commentary(content)
            if commentary:
                commentaries.append(f"=== עמוד {page[-1]} === {commentary}")
                logger.info("Found Steinsaltz commentary for %s", page)
            else:
                logger.warning("No Steinsaltz commentary in %s", file_path)
        else:
            logger.warning("File not found: %s", file_path)

    if commentaries:
        return " ".join(commentaries)

    return None
