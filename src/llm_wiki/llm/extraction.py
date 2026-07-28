"""Grammar-constrained structured extraction via `outlines` (ARCHITECTURE.md §4).

Uses Pydantic models from `llm_wiki.models` (e.g. `NoteFrontmatter`) as
the output schema, so extraction is guaranteed-valid JSON rather than
regex-scraped best-effort output.
"""

import outlines
from pydantic import BaseModel

from llm_wiki.llm.client import LlamaClient


def extract_structured[T: BaseModel](
    client: LlamaClient,
    prompt: str,
    output_type: type[T],
    *,
    model: str,
) -> T:
    """Runs grammar-constrained generation against `client`, validated as `output_type`."""
    outlines_model = outlines.from_openai(client.raw, model)
    generator = outlines.Generator(outlines_model, output_type=output_type)
    result = generator(prompt)
    return output_type.model_validate_json(result)
