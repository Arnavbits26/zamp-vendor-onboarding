"""
Tiny event log. Every stage of the pipeline emits events here.

Today the CLI prints them. On Day 6 the same events are streamed to the
browser, which is what powers the 'live run view' without changing any
pipeline code.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional


@dataclass
class Event:
    stage: str            # e.g. "intake", "completeness", "documents"
    status: str           # started | done | info | warn | error
    message: str
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class RunLog:
    def __init__(self, on_event: Optional[Callable[[Event], None]] = None):
        self.events: list[Event] = []
        self.on_event = on_event

    def emit(self, stage: str, status: str, message: str, **data) -> Event:
        ev = Event(stage, status, message, data)
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)
        return ev
