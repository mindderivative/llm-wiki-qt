"""Deterministic `[[wikilink]]` backlinks appended to every note (Phase 18).

A top-level leaf module (not nested under `compiler/` or `vault/`) so
`compiler/compiler_engine.py` and `vault/reindex.py` can both import it
without triggering each other's package `__init__.py` -- `compiler`'s
`__init__` imports `compiler_engine`, which imports `vault.reindex`, so
anything shared between them has to sit outside both packages or the
import graph cycles back on itself.
"""

from llm_wiki.models import NoteFrontmatter

# Every note gets a deterministic backlink to `[[index]]` (plus its
# `sources`/extra targets) regardless of LLM behavior -- SCHEMA.md's
# wikilink rule only asks the LLM to do this *between* related
# entities/concepts, a best-effort layer on top of this guaranteed one.
RELATED_MARKER = "<!-- llm-wiki:related -->"


def strip_related_block(content: str) -> str:
    """Removes a previously-appended Related block, if present.

    `rfind()`, not `find()`: the block is always appended once at the true
    end, so matching the *last* occurrence avoids truncating real content
    if the marker text ever appears earlier (e.g. echoed in prose).
    """
    idx = content.rfind(RELATED_MARKER)
    return content[:idx].rstrip() if idx != -1 else content


def render_related_block(fm: NoteFrontmatter, extra_targets: list[str] | None = None) -> str:
    targets = ["index"] + [s for s in fm.sources if s != fm.slug]
    for target in extra_targets or []:
        if target != fm.slug and target not in targets:
            targets.append(target)
    lines = "\n".join(f"- [[{t}]]" for t in targets)
    return f"\n\n{RELATED_MARKER}\n## Related\n\n{lines}\n"
