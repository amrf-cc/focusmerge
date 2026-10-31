"""Two-stage pipeline: read + align + warp (producer thread) and fuse (this thread)."""
from __future__ import annotations

import json
import os
import queue
import resource
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np

from .align import Aligner
from .errors import Cancelled, FocusMergeError, FrameError, NotEnoughFrames
from .fuse import PyramidFuser
from .tiffio import read_rgb16, write_result

_DONE = object()
_SAFE_DIR_PREFIX = "focusmerge-"


@dataclass
class MergeOptions:
    out_path: str
    align: str = "auto"                  # "auto" | "none"
    sharpness: int = 2                   # 1..3
    delete_inputs: bool = False
    progress_file: Optional[str] = None
    result_file: Optional[str] = None
    expected: Optional[int] = None
    cancel_file: Optional[str] = None
    aligned_hook: Optional[Callable[[int, np.ndarray], None]] = None


@dataclass
class MergeResult:
    output: str
    frames: int
    aligned: bool
    warnings: List[str]
    seconds: float
    seconds_per_frame: float
    metadata: str
    peak_rss_mb: float
    timings: Dict[str, float] = field(default_factory=dict)


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def _write_progress(path: Optional[str], done: int, total: Optional[int], stage: str, message: str = "") -> None:
    if not path:
        return
    message = message.replace("|", "/").replace("\n", " ")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(f"{done}|{total if total else '?'}|{stage}|{message}")
        os.replace(tmp, path)
    except OSError:
        pass


def _delete_input(path: str, warnings: List[str]) -> None:
    if os.path.basename(os.path.dirname(os.path.abspath(path))).startswith(_SAFE_DIR_PREFIX):
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        warnings.append(f"{os.path.basename(path)} not deleted (outside a {_SAFE_DIR_PREFIX}* folder)")


def _put(q: "queue.Queue", item, stop: threading.Event) -> bool:
    while not stop.is_set():
        try:
            q.put(item, timeout=0.1)
            return True
        except queue.Full:
            continue
    return False


def run_merge(frames: Iterable[str], opts: MergeOptions) -> MergeResult:
    t_start = time.perf_counter()
    q: "queue.Queue" = queue.Queue(maxsize=2)
    stop = threading.Event()
    warnings: List[str] = []
    timings = {"wait": 0.0, "read": 0.0, "align": 0.0, "fuse": 0.0, "write": 0.0}
    first_path: List[str] = []

    def producer() -> None:
        try:
            aligner: Optional[Aligner] = None
            shape = None
            it = iter(frames)
            idx = -1
            while True:
                t0 = time.perf_counter()
                try:
                    path = next(it)                      # blocks while Lightroom renders the next frame
                except StopIteration:
                    break
                timings["wait"] += time.perf_counter() - t0
                idx += 1
                if stop.is_set():
                    return
                t0 = time.perf_counter()
                rgb = read_rgb16(path)
                timings["read"] += time.perf_counter() - t0
                if idx == 0:
                    first_path.append(path)
                    shape = rgb.shape[:2]
                    aligner = Aligner(shape, enabled=(opts.align != "none"))
                elif rgb.shape[:2] != shape:
                    raise FrameError(
                        f"{os.path.basename(path)}: dimensions {rgb.shape[1]}x{rgb.shape[0]} differ from "
                        f"the first frame ({shape[1]}x{shape[0]})")
                if opts.delete_inputs and idx > 0:
                    _delete_input(path, warnings)
                t0 = time.perf_counter()
                aligned, mask, warn = aligner.register(rgb)
                timings["align"] += time.perf_counter() - t0
                warnings.extend(warn)
                if not _put(q, ("frame", idx, aligned, mask), stop):
                    return
            _put(q, (_DONE,), stop)
        except BaseException as exc:      # forwarded to the consuming thread
            _put(q, ("error", exc), stop)

    thread = threading.Thread(target=producer, name="fm-producer", daemon=True)
    thread.start()
    fuser: Optional[PyramidFuser] = None
    count = 0
    try:
        while True:
            item = q.get()
            if item[0] is _DONE:
                break
            if item[0] == "error":
                raise item[1]
            _, idx, aligned, mask = item
            if opts.aligned_hook:
                opts.aligned_hook(idx, aligned)
            t0 = time.perf_counter()
            if fuser is None:
                fuser = PyramidFuser(aligned.shape[:2], opts.sharpness)
            img = np.multiply(aligned, np.float32(1.0 / 65535.0), dtype=np.float32)
            fuser.add(img, mask)
            timings["fuse"] += time.perf_counter() - t0
            count += 1
            _write_progress(opts.progress_file, count, opts.expected, "fusing", f"frame {count}")
        if count < 2:
            raise NotEnoughFrames("need at least 2 frames to merge")
        _write_progress(opts.progress_file, count, opts.expected or count, "writing", "writing result")
        t0 = time.perf_counter()
        if opts.cancel_file and os.path.exists(opts.cancel_file):
            raise Cancelled("cancel requested")      # no result for a run the user abandoned
        result = fuser.result()
        if not np.isfinite(result).all():
            raise FocusMergeError("fusion produced non-finite values; refusing to write a damaged image")
        result *= 65535.0
        result += 0.5
        out16 = result.astype(np.uint16)
        metadata = write_result(first_path[0], opts.out_path, out16)
        timings["write"] = time.perf_counter() - t0
    except BaseException:
        stop.set()
        raise
    finally:
        stop.set()
        thread.join(timeout=5)

    if opts.delete_inputs and first_path:
        _delete_input(first_path[0], warnings)
    seconds = time.perf_counter() - t_start
    res = MergeResult(output=opts.out_path, frames=count, aligned=(opts.align != "none"),
                      warnings=warnings, seconds=seconds, seconds_per_frame=seconds / count,
                      metadata=metadata, peak_rss_mb=_peak_rss_mb(), timings=timings)
    _write_progress(opts.progress_file, count, count, "done", "finished")
    if opts.result_file:
        tmp = opts.result_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"output": res.output, "frames": res.frames, "aligned": res.aligned,
                       "warnings": res.warnings, "seconds": round(res.seconds, 3),
                       "seconds_per_frame": round(res.seconds_per_frame, 3),
                       "wait_seconds": round(timings["wait"], 3),
                       "busy_seconds": round(max(0.0, res.seconds - timings["wait"]), 3),
                       "metadata": res.metadata, "peak_rss_mb": round(res.peak_rss_mb, 1)}, f)
        os.replace(tmp, opts.result_file)
    return res
