"""Service to fetch Steinsaltz commentary from GitLab Sefaria pages or Sefaria URL API."""

import json
import logging
import re

import httpx

from audio_manager.infrastructure.gitlab_client import GitLabClient
from audio_manager.models.daf_text_fetcher import DafTextFetcher

logger = logging.getLogger(__name__)

SEFARIA_BASE_PATH = "backend/data/sefaria_pages"

# Regex to match HTML tags
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# Regex to match Hebrew nikud (vowel diacritics and cantillation marks)
NIQQUD_RE = re.compile(r"[\u0591-\u05C7]")

# Regex to match parentheses and their contents
PARENTHESES_PATTERN = re.compile(r"\([^)]*\)")

# Regex to extract text inside <b> tags
BOLD_TAG_PATTERN = re.compile(r"<b>([^<]*)</b>")


def remove_nikud(text: str) -> str:
    """Remove Hebrew nikud (vowel diacritics and cantillation marks) from text."""
    return NIQQUD_RE.sub("", text).strip()


# Abbreviation expansions (applied after nikud removal)
_ABBREV_MAP = {
    "מתני׳": "מתניתין",
    "גמ׳": "גמרא",
}


def clean_sefaria_line(line: str) -> str:
    """Strip HTML, remove nikud, and normalize Hebrew abbreviations."""
    return normalize_hebrew_text(remove_nikud(strip_html_tags(line)))


def normalize_hebrew_text(text: str) -> str:
    """Expand common Hebrew abbreviations and normalize punctuation."""
    for abbrev, expansion in _ABBREV_MAP.items():
        text = text.replace(abbrev, expansion)
    # Replace Hebrew gershayim with standard double quote
    text = text.replace("\u05f4", '"')
    return text


def strip_html_tags(text: str) -> str:
    """Remove all HTML tags, parentheses with contents, newlines, and unescape characters from text."""
    cleaned = HTML_TAG_PATTERN.sub("", text)
    # Remove parentheses and their contents
    cleaned = PARENTHESES_PATTERN.sub("", cleaned)
    # Remove brackets but keep the text inside
    cleaned = cleaned.replace("[", "").replace("]", "")
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

            # Extract only text inside <b> tags
            bold_texts = BOLD_TAG_PATTERN.findall(raw_text)
            extracted = " ".join(bold_texts)
            # Attach standalone letters to the next word
            extracted = re.sub(r"(^|\s)(\S)\s+(?=\S)", r"\1\2", extracted)
            # Clean up the extracted text
            return strip_html_tags(extracted)

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
                commentaries.append(commentary)
                logger.info("Found Steinsaltz commentary for %s", page)
            else:
                logger.warning("No Steinsaltz commentary in %s", file_path)
        else:
            logger.warning("File not found: %s", file_path)

    if commentaries:
        return " ".join(commentaries)

    return None


class GitLabDafTextFetcher(DafTextFetcher):
    """Fetches Steinsaltz commentary from GitLab-hosted Sefaria JSON files."""

    def __init__(self, gitlab_client: GitLabClient, branch: str = "main") -> None:
        self._gitlab_client = gitlab_client
        self._branch = branch

    def fetch_for_daf(self, massechet_name: str, daf_id: int) -> str | None:
        # GitLab paths use lowercase folder names
        return fetch_steinsaltz_for_daf(
            self._gitlab_client, massechet_name.lower(), daf_id, self._branch
        )


class SefariaUrlDafTextFetcher(DafTextFetcher):
    """Fetches Steinsaltz commentary directly from the Sefaria v3 REST API."""

    _BASE = "https://www.sefaria.org.il"
    _PATH = "/api/v3/texts/{}.{}{}"
    _PARAMS = "version=primary&fill_in_missing_segments=1&return_format=wrap_all_entities"

    def _build_url(self, massechet_name: str, daf_id: int, amud: str) -> str:
        path = self._PATH.format(massechet_name, daf_id, amud)
        return f"{self._BASE}{path}?{self._PARAMS}"

    def fetch_for_daf(self, massechet_name: str, daf_id: int) -> str | None:
        texts = []
        for amud_letter in ("A", "B"):
            text = self._fetch_amud(massechet_name, daf_id, amud_letter)
            if text:
                texts.append(text)
            else:
                logger.warning(
                    "No text for %s %d amud %s", massechet_name, daf_id, amud_letter
                )

        if not texts:
            logger.error(
                "No text found for %s daf %d (both amuds missing)",
                massechet_name,
                daf_id,
            )
            raise ValueError(
                f"No Sefaria text found for {massechet_name} daf {daf_id}"
            )

        return " ".join(texts)

    def _fetch_amud(self, massechet_name: str, daf_id: int, amud: str) -> str | None:
        url = self._build_url(massechet_name, daf_id, amud)
        logger.info("Fetching Sefaria amud: %s", url)
        try:
            response = httpx.get(url, timeout=30)
            response.raise_for_status()
            versions = response.json().get("versions", [])
            if not versions:
                return None
            lines = versions[0].get("text", [])
            clean = [clean_sefaria_line(line) for line in lines if isinstance(line, str)]
            return " ".join(clean).strip() or None
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", url, e)
            return None
