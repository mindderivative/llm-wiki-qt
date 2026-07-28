"""Phase 6: asset shielding, GEO word bounding, and frontmatter generation."""

import pytest

from llm_wiki.ingest.atomizer import (
    MAX_WORDS,
    MIN_WORDS,
    atomize,
    count_words,
    generate_geo_frontmatter,
    shield_assets,
    unshield_assets,
)
from llm_wiki.models import Chunk

_DOC_WITH_CODE_AND_TABLE = """# Doc

Some intro text.

```python
def foo():
    return 42
```

| Col A | Col B |
| --- | --- |
| 1 | 2 |

More text after.
"""


def _words(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


def test_shield_assets_hides_code_and_tables() -> None:
    shielded, asset_map = shield_assets(_DOC_WITH_CODE_AND_TABLE)

    assert "```" not in shielded
    assert "| Col A" not in shielded
    assert len(asset_map) == 2


def test_shield_and_unshield_round_trips_exactly() -> None:
    shielded, asset_map = shield_assets(_DOC_WITH_CODE_AND_TABLE)
    restored = unshield_assets(shielded, asset_map)

    assert restored == _DOC_WITH_CODE_AND_TABLE


def test_count_words_ignores_shielded_assets() -> None:
    shielded, _ = shield_assets(_DOC_WITH_CODE_AND_TABLE)
    # "Doc" + "Some intro text." + "More text after."
    assert count_words(shielded) == 7


def test_count_words_excludes_markdown_syntax() -> None:
    text = (
        "# Heading\n\n"
        "This is **bold** and *italic* and `code` and a "
        "[link](http://example.com) here."
    )
    assert count_words(text) == 12


def test_oversized_section_gets_split() -> None:
    paragraphs = [_words(f"p{p}w", 150) for p in range(6)]
    text = "# Big Section\n\n" + "\n\n".join(paragraphs) + "\n"

    chunks = atomize(text, queue_item_id=1)

    assert len(chunks) == 3
    assert sum(c.word_count for c in chunks) == 900
    for chunk in chunks:
        assert chunk.word_count <= MAX_WORDS
        assert "Big Section" in chunk.title
    assert [c.ordinal for c in chunks] == [0, 1, 2]


def test_undersized_sections_get_merged() -> None:
    text = (
        "## A\n\n"
        + _words("a", 80)
        + "\n\n## B\n\n"
        + _words("b", 80)
        + "\n\n## C\n\n"
        + _words("c", 80)
        + "\n"
    )

    chunks = atomize(text, queue_item_id=1)

    assert len(chunks) == 1
    assert chunks[0].word_count == 240
    assert chunks[0].title == "A"
    assert "b0" in chunks[0].content
    assert "c0" in chunks[0].content


def test_trailing_undersized_section_folds_into_previous_chunk() -> None:
    text = "## Big\n\n" + _words("x", 300) + "\n\n## Tiny\n\n" + _words("y", 50) + "\n"

    chunks = atomize(text, queue_item_id=1)

    assert len(chunks) == 1
    assert chunks[0].word_count == 350
    assert chunks[0].title == "Big"
    assert "y0" in chunks[0].content


def test_well_bounded_section_passes_through_unchanged() -> None:
    text = "# Just Right\n\n" + _words("w", 250) + "\n"

    chunks = atomize(text, queue_item_id=1)

    assert len(chunks) == 1
    assert MIN_WORDS <= chunks[0].word_count <= MAX_WORDS
    assert chunks[0].title == "Just Right"


def test_atomize_requires_exactly_one_owner() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        atomize("# X\n\nbody", note_id=1, queue_item_id=1)
    with pytest.raises(ValueError, match="exactly one"):
        atomize("# X\n\nbody")


def test_atomize_assigns_owner_and_sequential_ordinals() -> None:
    text = "## A\n\n" + _words("a", 250) + "\n\n## B\n\n" + _words("b", 250) + "\n"

    chunks = atomize(text, note_id=7)

    assert [c.note_id for c in chunks] == [7, 7]
    assert [c.queue_item_id for c in chunks] == [None, None]
    assert [c.ordinal for c in chunks] == [0, 1]


def test_generate_geo_frontmatter() -> None:
    chunk = Chunk(queue_item_id=1, ordinal=0, title="Foo", content="bar", word_count=200)

    frontmatter = generate_geo_frontmatter(chunk, tags=["source", "geo"])

    assert frontmatter.startswith("---\n")
    assert frontmatter.endswith("---\n")
    assert 'title: "Foo"' in frontmatter
    assert "ordinal: 0" in frontmatter
    assert "word_count: 200" in frontmatter
    assert 'tags: ["source", "geo"]' in frontmatter
