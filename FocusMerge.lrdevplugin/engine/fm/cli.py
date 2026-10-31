"""Command line: `python -m fm merge …` and `python -m fm selftest`."""
from __future__ import annotations

import argparse
import sys
import traceback
from typing import List, Optional

from . import __version__
from .errors import FocusMergeError
from .frames_queue import follow_frames, static_frames
from .pipeline import MergeOptions, run_merge


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fm", description="FocusMerge engine")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="merge a focus stack")
    m.add_argument("frames", nargs="*", help="frame files, in order")
    m.add_argument("--frames-file", help="queue file with one path per line")
    m.add_argument("--follow", action="store_true", help="keep reading the queue file until #END")
    m.add_argument("--expected", type=int, help="number of frames to expect (progress only)")
    m.add_argument("--out", required=True, help="output TIFF path")
    m.add_argument("--align", choices=["auto", "none"], default="auto")
    m.add_argument("--sharpness", type=int, choices=[1, 2, 3], default=2)
    m.add_argument("--delete-inputs", action="store_true")
    m.add_argument("--progress-file")
    m.add_argument("--result-file")
    m.add_argument("--cancel-file")
    m.add_argument("--error-file", help="write the failure reason here (one line)")
    m.add_argument("--idle-timeout", type=float, default=900.0)
    sub.add_parser("selftest", help="merge a tiny generated stack")
    return p


def _merge(args, parser) -> int:
    if bool(args.frames) == bool(args.frames_file):
        parser.error("give either frame files or --frames-file")
    if args.frames_file:
        source = follow_frames(args.frames_file, follow=args.follow, cancel_file=args.cancel_file,
                               idle_timeout=args.idle_timeout)
    else:
        source = static_frames(args.frames)
    opts = MergeOptions(out_path=args.out, align=args.align, sharpness=args.sharpness,
                        delete_inputs=args.delete_inputs, progress_file=args.progress_file,
                        result_file=args.result_file, expected=args.expected,
                        cancel_file=args.cancel_file)
    res = run_merge(source, opts)
    wait = res.timings.get("wait", 0.0)
    print(f"merged {res.frames} frames in {res.seconds:.1f}s, of which {wait:.1f}s waiting for frames "
          f"to arrive, {max(0.0, res.seconds - wait):.1f}s working "
          f"(metadata={res.metadata}) -> {res.output}")
    for w in res.warnings:
        print(f"warning: {w}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    error_file = getattr(args, "error_file", None)

    def report(message: str) -> None:
        print(f"error: {message}", file=sys.stderr)
        if error_file:
            try:
                with open(error_file, "w") as f:
                    f.write(message.replace("\n", " ") + "\n")
            except OSError:
                pass

    try:
        if args.cmd == "selftest":
            from .selftest import run
            return run()
        return _merge(args, parser)
    except FocusMergeError as exc:
        report(str(exc))
        return exc.exit_code
    except Exception as exc:
        traceback.print_exc()
        report(f"internal error: {exc}")
        return 6
