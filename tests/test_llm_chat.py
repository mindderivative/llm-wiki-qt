"""Phase 15d: llm.chat.ask() -- RAG-style chat grounded in vault chunks."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.llm import ask
from llm_wiki.llm.client import LlamaClient
from llm_wiki.models import Chunk
from llm_wiki.storage import connect, insert_chunk, upsert_chunk_embedding


class _FakeEmbeddings:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def create(self, **kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=self._vector) for _ in kwargs["input"]]
        )


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeOpenAI:
    def __init__(self, *, embedding: list[float], answer: str) -> None:
        self.embeddings = _FakeEmbeddings(embedding)
        self.chat = SimpleNamespace(completions=_FakeCompletions(answer))


def _embedding(first: float) -> list[float]:
    return [first, *([0.0] * 767)]


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "db.sqlite3")
    yield connection
    connection.close()


def test_ask_grounds_the_answer_in_the_nearest_chunk(conn) -> None:
    conn.execute(
        "INSERT INTO queue (title, raw_path, status, created_at, updated_at) "
        "VALUES ('Doc', 'raw/doc.md', 'queued', '2026-01-01', '2026-01-01')"
    )
    queue_item_id = conn.execute("SELECT id FROM queue").fetchone()[0]
    chunk = Chunk(
        queue_item_id=queue_item_id,
        ordinal=0,
        title="Intro",
        content="Ada Lovelace wrote the first algorithm.",
        word_count=6,
    )
    chunk_id = insert_chunk(conn, chunk)
    upsert_chunk_embedding(conn, chunk_id, _embedding(1.0))

    fake = _FakeOpenAI(embedding=_embedding(1.0), answer="Ada Lovelace, per the notes.")
    client = LlamaClient(client=fake)

    answer = ask(conn, client, "Who wrote the first algorithm?", chat_model="test-model")

    assert answer == "Ada Lovelace, per the notes."
    sent_messages = fake.chat.completions.last_kwargs["messages"]
    user_message = sent_messages[-1]["content"]
    assert "Ada Lovelace wrote the first algorithm." in user_message
    assert "Who wrote the first algorithm?" in user_message


def test_ask_with_no_chunks_still_answers(conn) -> None:
    fake = _FakeOpenAI(embedding=_embedding(1.0), answer="I don't have that information.")
    client = LlamaClient(client=fake)

    answer = ask(conn, client, "Anything in here?", chat_model="test-model")

    assert answer == "I don't have that information."
    user_message = fake.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "no relevant context found" in user_message


def test_ask_passes_the_requested_chat_model(conn) -> None:
    fake = _FakeOpenAI(embedding=_embedding(1.0), answer="ok")
    client = LlamaClient(client=fake)

    ask(conn, client, "hello", chat_model="qwen3-8b")

    assert fake.chat.completions.last_kwargs["model"] == "qwen3-8b"
