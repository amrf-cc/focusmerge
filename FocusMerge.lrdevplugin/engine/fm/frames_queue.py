"""Ordered frame sources: a fixed list, or a queue file that grows while we read it."""
from __future__ import annotations

import os
import time
from typing import Iterator, Optional, Sequence

from .errors import Cancelled, QueueTimeout

END = "#END"
ABORT = "#ABORT"


def static_frames(paths: Sequence[str]) -> Iterator[str]:
    yield from paths


def follow_frames(queue_file: str, *, follow: bool = True, cancel_file: Optional[str] = None,
                  idle_timeout: float = 900.0, poll: float = 0.05,
                  sleep=time.sleep, clock=time.monotonic) -> Iterator[str]:
    """Yield frame paths from `queue_file` as lines appear.

    Only newline-terminated lines are consumed while following. `#END` finishes, `#ABORT` or the
    cancel file raises Cancelled, `idle_timeout` seconds without growth raises QueueTimeout.
    With follow=False the file is read to EOF (a final unterminated line is accepted).
    """
    offset = 0
    pending = b""
    last_growth = clock()
    while True:
        if cancel_file and os.path.exists(cancel_file):
            raise Cancelled("cancel requested")
        chunk = b""
        try:
            with open(queue_file, "rb") as f:
                f.seek(offset)
                chunk = f.read()
        except FileNotFoundError:
            pass
        if chunk:
            offset += len(chunk)
            pending += chunk
            last_growth = clock()
            *lines, pending = pending.split(b"\n")
            for raw in lines:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                if line == END:
                    return
                if line == ABORT:
                    raise Cancelled("abort requested")
                if line.startswith("#"):
                    continue
                yield line
            continue
        if not follow:
            tail = pending.decode("utf-8").strip()
            if tail and not tail.startswith("#"):
                yield tail
            return
        if clock() - last_growth > idle_timeout:
            raise QueueTimeout(f"no new frames for {idle_timeout:.0f}s")
        sleep(poll)
