"""Header-boundary GEO chunking (ARCHITECTURE.md's GEO standards).

Splits raw Markdown at heading boundaries, shields code fences/tables so
they're never mangled or miscounted, bounds each resulting section to
200-400 words (splitting oversized sections, merging undersized ones),
and emits `Chunk` models plus a per-chunk GEO frontmatter header.
"""

import itertools
import re

from llm_wiki.models import Chunk

MIN_WORDS = 200
MAX_WORDS = 400

_ASSET_PLACEHOLDER_RE = re.compile(r"__LLM_WIKI_ASSET_\d+__")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_MARKER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE_MARKER_RE = re.compile(r"^>\s?", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.MULTILINE)
_BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)\*(?!\*)|(?<!_)_(?!_)(.*?)_(?!_)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")


def shield_assets(text: str) -> tuple[str, dict[str, str]]:
    """Replaces fenced code blocks and Markdown tables with deterministic placeholders.

    Code fences are shielded first so any table-like or heading-like text
    inside them can never be misread as real structure once tables/headings
    are located on the already-shielded text.
    """
    asset_map: dict[str, str] = {}
    counter = itertools.count()

    def _shield_match(match: re.Match[str]) -> str:
        placeholder = f"__LLM_WIKI_ASSET_{next(counter)}__"
        asset_map[placeholder] = match.group(0)
        return placeholder

    shielded = _CODE_FENCE_RE.sub(_shield_match, text)
    shielded = _shield_tables(shielded, asset_map, counter)
    return shielded, asset_map


def _shield_tables(text: str, asset_map: dict[str, str], counter: itertools.count) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if (
            i + 1 < len(lines)
            and _TABLE_ROW_RE.match(lines[i])
            and _TABLE_SEP_RE.match(lines[i + 1])
        ):
            start = i
            i += 2
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                i += 1
            block = "\n".join(lines[start:i])
            placeholder = f"__LLM_WIKI_ASSET_{next(counter)}__"
            asset_map[placeholder] = block
            out.append(placeholder)
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def unshield_assets(text: str, asset_map: dict[str, str]) -> str:
    """Restores placeholders produced by `shield_assets()` to their original text."""
    for placeholder, original in asset_map.items():
        text = text.replace(placeholder, original)
    return text


def count_words(text: str) -> int:
    """Counts prose words, excluding Markdown syntax and shielded asset placeholders."""
    text = _ASSET_PLACEHOLDER_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HEADING_MARKER_RE.sub("", text)
    text = _BLOCKQUOTE_MARKER_RE.sub("", text)
    text = _LIST_MARKER_RE.sub("", text)
    text = _BOLD_RE.sub(r"\2", text)
    text = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    return len(text.split())


def _split_into_sections(shielded_text: str) -> list[tuple[str, str]]:
    """Splits shielded Markdown into (heading title, section body) pairs at ATX headings."""
    sections: list[tuple[str, list[str]]] = []
    current_title = "Introduction"
    current_lines: list[str] = []

    for line in shielded_text.split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            if any(line_.strip() for line_ in current_lines):
                sections.append((current_title, current_lines))
            current_title = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if any(line_.strip() for line_ in current_lines) or not sections:
        sections.append((current_title, current_lines))

    return [(title, "\n".join(body_lines).strip()) for title, body_lines in sections]


def _split_oversized(title: str, body: str) -> list[tuple[str, str]]:
    if count_words(body) <= MAX_WORDS:
        return [(title, body)]

    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    parts: list[tuple[str, str]] = []
    current_paragraphs: list[str] = []
    current_words = 0

    for paragraph in paragraphs:
        paragraph_words = count_words(paragraph)
        if current_paragraphs and current_words + paragraph_words > MAX_WORDS:
            parts.append((f"{title} (part {len(parts) + 1})", "\n\n".join(current_paragraphs)))
            current_paragraphs, current_words = [], 0
        current_paragraphs.append(paragraph)
        current_words += paragraph_words

    if current_paragraphs:
        parts.append((f"{title} (part {len(parts) + 1})", "\n\n".join(current_paragraphs)))

    if len(parts) <= 1:
        # A single paragraph that's still oversized -- nothing more to
        # split without cutting mid-sentence, so leave it whole.
        return [(title, body)]
    return parts


def _bound_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    split_sections = [part for title, body in sections for part in _split_oversized(title, body)]

    merged: list[tuple[str, str]] = []
    pending_title: str | None = None
    pending_body = ""

    for title, body in split_sections:
        if pending_title is None:
            pending_title, pending_body = title, body
        else:
            pending_body = f"{pending_body}\n\n{body}"

        if count_words(pending_body) >= MIN_WORDS:
            merged.append((pending_title, pending_body))
            pending_title, pending_body = None, ""

    if pending_title is not None:
        if merged:
            last_title, last_body = merged.pop()
            merged.append((last_title, f"{last_body}\n\n{pending_body}"))
        else:
            merged.append((pending_title, pending_body))

    return merged


def atomize(
    text: str,
    *,
    note_id: int | None = None,
    queue_item_id: int | None = None,
) -> list[Chunk]:
    """Splits raw Markdown into GEO-bounded atomic `Chunk`s (200-400 words each)."""
    if (note_id is None) == (queue_item_id is None):
        raise ValueError("atomize() requires exactly one of note_id or queue_item_id")

    shielded, asset_map = shield_assets(text)
    bounded = _bound_sections(_split_into_sections(shielded))

    chunks: list[Chunk] = []
    for ordinal, (title, shielded_body) in enumerate(bounded):
        chunks.append(
            Chunk(
                note_id=note_id,
                queue_item_id=queue_item_id,
                ordinal=ordinal,
                title=title,
                content=unshield_assets(shielded_body, asset_map).strip(),
                word_count=count_words(shielded_body),
            )
        )
    return chunks


def generate_geo_frontmatter(chunk: Chunk, *, tags: list[str] | None = None) -> str:
    """Renders a GEO-compliant YAML frontmatter header for one atomic chunk."""
    tag_list = ", ".join(f'"{tag}"' for tag in (tags or []))
    return (
        "---\n"
        f'title: "{chunk.title}"\n'
        f"ordinal: {chunk.ordinal}\n"
        f"word_count: {chunk.word_count}\n"
        f"tags: [{tag_list}]\n"
        "---\n"
    )
