"""Environment check: merge three small generated frames and verify the output."""
from __future__ import annotations

import os
import tempfile
import time

import cv2
import numpy as np
import tifffile

from .frames_queue import static_frames
from .pipeline import MergeOptions, run_merge


def run() -> int:
    rng = np.random.default_rng(0)
    h, w = 192, 256
    sharp = cv2.GaussianBlur(rng.random((h, w, 3)).astype(np.float32), (0, 0), 1.2)
    soft = cv2.GaussianBlur(sharp, (0, 0), 3.0)
    left, right = soft.copy(), soft.copy()
    left[:, : w // 2] = sharp[:, : w // 2]
    right[:, w // 2:] = sharp[:, w // 2:]
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="focusmerge-selftest-") as d:
        paths = []
        for i, img in enumerate((left, right, soft)):
            p = os.path.join(d, f"f{i}.tif")
            tifffile.imwrite(p, np.clip(img * 65535, 0, 65535).astype(np.uint16), photometric="rgb")
            paths.append(p)
        out = os.path.join(d, "out.tif")
        res = run_merge(static_frames(paths), MergeOptions(out_path=out))
        merged = tifffile.imread(out).astype(np.float32) / 65535.0
    err_fused = float(np.abs(merged - sharp).mean())
    err_left = float(np.abs(left - sharp).mean())
    ok = res.frames == 3 and err_fused < err_left
    print(f"selftest {'ok' if ok else 'FAILED'}: 3 frames, mean error {err_fused:.4f} "
          f"(best single frame {err_left:.4f}), {time.perf_counter() - t0:.1f}s")
    return 0 if ok else 6
