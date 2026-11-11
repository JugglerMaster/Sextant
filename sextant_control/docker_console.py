"""In-memory broker for interactive Docker console sessions.

This is deliberately single-process state. SQLite stores session metadata only;
stdin/stdout are bounded in-memory queues and are never persisted.
"""

from __future__ import annotations

import base64
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


class ConsoleError(ValueError):
    pass


@dataclass
class ConsoleState:
    session_id: str
    agent_id: str
    created_by: str
    status: str = "opening"
    rows: int = 32
    cols: int = 100
    input: deque[bytes] = field(default_factory=deque)
    resize: tuple[int, int] | None = None
    output: deque[tuple[int, bytes]] = field(default_factory=deque)
    next_seq: int = 1
    output_bytes: int = 0
    close_requested: bool = False
    closed: bool = False
    exit_code: int | None = None
    error: str = ""
    last_activity: float = field(default_factory=time.monotonic)
    last_poll: float = field(default_factory=time.monotonic)
    last_input: float = 0.0
    condition: threading.Condition = field(default_factory=threading.Condition)


class ConsoleBroker:
    def __init__(
        self,
        *,
        max_per_agent: int = 2,
        max_input_bytes: int = 65536,
        max_output_bytes: int = 1_048_576,
        input_interval_seconds: float = 0.015,
    ):
        self.max_per_agent = max(1, min(int(max_per_agent), 8))
        self.max_input_bytes = max(1024, int(max_input_bytes))
        self.max_output_bytes = max(4096, int(max_output_bytes))
        self.input_interval_seconds = max(0.0, float(input_interval_seconds))
        self._lock = threading.Lock()
        self._sessions: dict[str, ConsoleState] = {}

    def create(
        self,
        session_id: str,
        *,
        agent_id: str,
        created_by: str,
        rows: int,
        cols: int,
    ) -> ConsoleState:
        with self._lock:
            active = sum(
                1
                for state in self._sessions.values()
                if state.agent_id == agent_id and not state.closed
            )
            if active >= self.max_per_agent:
                raise ConsoleError("Maximum concurrent consoles reached for this agent.")
            state = ConsoleState(
                session_id=session_id,
                agent_id=agent_id,
                created_by=created_by,
                rows=rows,
                cols=cols,
            )
            self._sessions[session_id] = state
            return state

    def get(self, session_id: str) -> ConsoleState | None:
        with self._lock:
            return self._sessions.get(session_id)

    def active_count(self, agent_id: str) -> int:
        with self._lock:
            return sum(
                1
                for state in self._sessions.values()
                if state.agent_id == agent_id and not state.closed
            )

    def mark_open(self, session_id: str) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or state.closed:
                return
            state.status = "open"
            state.last_poll = time.monotonic()
            state.last_activity = time.monotonic()
        with state.condition:
            state.condition.notify_all()

    def mark_poll(self, session_id: str) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.last_poll = time.monotonic()
        with state.condition:
            state.condition.notify_all()

    def push_input(self, session_id: str, data: bytes) -> str:
        if not data:
            return "ok"
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or state.closed or state.close_requested:
                return "closed"
            now = time.monotonic()
            if now - state.last_input < self.input_interval_seconds:
                return "rate"
            pending = sum(len(item) for item in state.input)
            if pending + len(data) > self.max_input_bytes:
                return "full"
            state.input.append(bytes(data))
            state.last_input = now
            state.last_activity = now
        with state.condition:
            state.condition.notify_all()
        return "ok"

    def push_resize(self, session_id: str, rows: int, cols: int) -> str:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or state.closed or state.close_requested:
                return "closed"
            state.resize = (rows, cols)
            state.last_activity = time.monotonic()
        with state.condition:
            state.condition.notify_all()
        return "ok"

    def push_output(self, session_id: str, data: bytes) -> bool:
        if not data:
            return True
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or state.closed:
                return False
            state.output.append((state.next_seq, bytes(data)))
            state.next_seq += 1
            state.output_bytes += len(data)
            while state.output and state.output_bytes > self.max_output_bytes:
                _, old = state.output.popleft()
                state.output_bytes -= len(old)
            state.last_activity = time.monotonic()
        with state.condition:
            state.condition.notify_all()
        return True

    def poll(self, session_id: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
        with state.condition:
            while True:
                with self._lock:
                    if state.closed or state.close_requested:
                        return {"type": "close"}
                    if state.input:
                        data = state.input.popleft()
                        return {"type": "input", "data": base64.b64encode(data).decode("ascii")}
                    if state.resize is not None:
                        rows, cols = state.resize
                        state.resize = None
                        return {"type": "resize", "rows": rows, "cols": cols}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                state.condition.wait(timeout=min(remaining, 1.0))

    def outputs_since(self, session_id: str, seq: int) -> list[tuple[int, bytes]]:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return []
            return [(chunk_seq, data) for chunk_seq, data in state.output if chunk_seq > seq]

    def status(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
            return {
                "session_id": session_id,
                "status": state.status,
                "exit_code": state.exit_code,
                "error": state.error,
                "rows": state.rows,
                "cols": state.cols,
            }

    def request_close(self, session_id: str, *, status: str = "closing", error: str = "") -> bool:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or state.closed:
                return False
            state.close_requested = True
            state.status = status
            if error:
                state.error = error[:4000]
        with state.condition:
            state.condition.notify_all()
        return True

    def finish(self, session_id: str, *, status: str, exit_code: int | None, error: str = "") -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.status = status
            state.exit_code = exit_code
            state.error = error[:4000]
            state.closed = True
            state.close_requested = True
        with state.condition:
            state.condition.notify_all()

    def close_for_agent(self, agent_id: str, *, status: str, error: str) -> int:
        with self._lock:
            states = [
                state
                for state in self._sessions.values()
                if state.agent_id == agent_id and not state.closed
            ]
        for state in states:
            self.finish(state.session_id, status=status, exit_code=None, error=error)
        return len(states)

    def close_for_user(self, username: str, *, status: str = "closed", error: str = "") -> int:
        with self._lock:
            states = [
                state
                for state in self._sessions.values()
                if state.created_by == username and not state.closed
            ]
        for state in states:
            self.finish(state.session_id, status=status, exit_code=None, error=error)
        return len(states)

    def prune(
        self,
        *,
        idle_seconds: int,
        poll_seconds: int,
        now: float | None = None,
    ) -> list[tuple[str, str, str]]:
        now = now or time.monotonic()
        closed: list[tuple[str, str, str]] = []
        with self._lock:
            states = list(self._sessions.values())
        for state in states:
            if state.closed:
                continue
            if now - state.last_activity > max(1, idle_seconds):
                self.finish(state.session_id, status="failed", exit_code=None, error="Console idle timeout.")
                closed.append((state.session_id, "failed", "Console idle timeout."))
            elif now - state.last_poll > max(30, poll_seconds * 2 + 10):
                self.finish(state.session_id, status="failed", exit_code=None, error="Agent console poll missed.")
                closed.append((state.session_id, "failed", "Agent console poll missed."))
        return closed

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
