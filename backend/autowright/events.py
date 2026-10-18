"""In-process event hub feeding the §19 WebSocket. Thread-safe publish."""
from __future__ import annotations

import asyncio
import threading
from typing import Any


# Sentinel a stalled subscriber's queue gets when it overflows — the WS
# handler closes that socket so the client reconnects and re-syncs (§19: a
# silent drop of e.g. exec.finished would wedge the UI "executing" forever).
OVERFLOW: dict = {"event": "__overflow__"}


class EventHub:
    def __init__(self) -> None:
        # Each subscriber queue maps to its §19 role ("shell" for the
        # Electron main process, "renderer" otherwise). Roles grant nothing:
        # every subscriber receives every event; the §6 notification carrier
        # rule only asks whether a shell is connected.
        self._subs: dict[asyncio.Queue, str] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, role: str = "renderer") -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs[q] = role
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.pop(q, None)

    def has_role(self, role: str) -> bool:
        """Whether any subscriber with `role` is connected right now (§6
        carrier rule: callable from any thread)."""
        with self._lock:
            return role in self._subs.values()

    def publish(self, event: str, **payload: Any) -> None:
        """Callable from any thread (engine worker threads publish log lines)."""
        msg = {"event": event, **payload}
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, msg)
        except RuntimeError:
            # Loop already closed (shutdown racing a live engine/listener
            # thread). Nobody is listening; a publish must never raise into
            # a worker's finalizer.
            pass

    def _fanout(self, msg: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Stop feeding the stalled subscriber and poison its queue —
                # its socket closes and the client reconnects into a resync.
                self.unsubscribe(q)
                try:
                    q.get_nowait()  # make room so the sentinel always lands
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(OVERFLOW)
                except asyncio.QueueFull:
                    pass


hub = EventHub()
