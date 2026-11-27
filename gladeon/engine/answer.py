"""One chat answer with post-filter and the empty retry (8.6.3-8.6.4)."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from ..prompts import post_filter
from .client import Cancelled, ChatParams, DeadlineExceeded, Engine, EngineError

EMPTY_RETRY_BUMP = 0.3
MIN_RETRY_S = 5.0


@dataclass
class Answer:
    text: str                 # post-filtered; "" means use the Engine-empty reply
    attempts: int
    timed_out: bool = False
    error: Optional[str] = None


def answer(engine: Engine, messages: list[dict], params: ChatParams, deadline: float,
           control_tokens: Sequence[str], tag: Optional[str] = None,
           cancel: Optional[threading.Event] = None,
           on_text: Optional[Callable[[str], bool]] = None) -> Answer:
    attempts = 0
    bump = 0.0
    while True:
        attempts += 1
        p = ChatParams(**{**params.__dict__, "temperature_bump": bump})
        try:
            r = engine.chat(messages, p, deadline, cancel, on_text)
        except DeadlineExceeded:
            return Answer("", attempts, timed_out=True)
        except Cancelled:
            return Answer("", attempts, error="CANCELLED")
        except EngineError as e:
            return Answer("", attempts, error=str(e))
        text = post_filter(r.text, control_tokens, tag or "")
        if text.strip() or r.finish_reason == "client_stop":
            return Answer(text, attempts)
        if attempts >= 2 or deadline - engine.clock() < MIN_RETRY_S:
            return Answer("", attempts)
        bump = EMPTY_RETRY_BUMP
