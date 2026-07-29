"""loguru -> Flet bridge (ARCHITECTURE.md §10).

The engine itself stays Qt/Flet-free -- this bridge just subscribes to
whatever `loguru.logger` emits process-wide. A single loguru sink is
registered once (idempotent) and fans out to every subscriber, so multiple
`LogPanel` instances (e.g. across tests) never register duplicate loguru
handlers.
"""

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class LogLine:
    time: str
    level: str
    message: str


_listeners: list[Callable[[LogLine], None]] = []
_handler_id: int | None = None


def _dispatch(message: object) -> None:
    record = message.record  # type: ignore[attr-defined]
    line = LogLine(
        time=record["time"].strftime("%H:%M:%S"),
        level=record["level"].name,
        message=record["message"],
    )
    for listener in list(_listeners):
        listener(line)


def _ensure_sink_registered() -> None:
    global _handler_id
    if _handler_id is not None:
        return
    _handler_id = logger.add(_dispatch, level="INFO")


def subscribe(listener: Callable[[LogLine], None]) -> None:
    """Registers a callback fired for every future loguru record."""
    _ensure_sink_registered()
    _listeners.append(listener)
