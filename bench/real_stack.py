"""Run the engine on real frames; report speed, memory and a sharpness-based quality check; save crops."""
import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "FocusMerge.lrdevplugin", "engine"))
from fm.frames_queue import static_frames  # noqa: E402
from fm.pipeline import MergeOptions, run_merge  # noqa: E402
from fm.tiffio import read_rgb16  # noqa: E402

TILE = 64


def tile_sharpness(rgb16, pre_blur=1.2):
    """Per-tile Laplacian variance of the luminance; pre_blur suppresses sensor noise so the
    figure tracks real detail (raw Laplacian variance is inflated by noise)."""
    gray = cv2.cvtColor(rgb16, cv2.COLOR_RGB2GRAY).astype(np.float32) * (1.0 / 65535.0)
    if pre_blur:
        gray = cv2.GaussianBlur(gray, (0, 0), pre_blur)
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    h, w = (lap.shape[0] // TILE) * TILE, (lap.shape[1] // TILE) * TILE
    return lap[:h, :w].reshape(h // TILE, TILE, w // TILE, TILE).var(axis=(1, 3))


def save_crop(path, rgb16, box):
    y, x, h, w = box
    crop = rgb16[y:y + h, x:x + w]
    cv2.imwrite(path, cv2.cvtColor((crop >> 8).astype(np.uint8), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 92])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--crops", default=None)
    ap.add_argument("--sharpness", type=int, default=2)
    ap.add_argument("--align", default="auto")
    ap.add_argument("frames", nargs="+")
    a = ap.parse_args()

    per_frame = []
    opts = MergeOptions(out_path=a.out, sharpness=a.sharpness, align=a.align,
                        aligned_hook=lambda i, img: per_frame.append(tile_sharpness(img)))
    t0 = time.perf_counter()
    res = run_merge(static_frames(a.frames), opts)
    wall = time.perf_counter() - t0
    print(f"frames={res.frames} wall={wall:.1f}s per_frame={res.seconds_per_frame:.2f}s "
          f"peak_rss={res.peak_rss_mb:.0f}MB metadata={res.metadata}")
    print("timings(s, summed over threads):", {k: round(v, 1) for k, v in res.timings.items()})
    for w in res.warnings:
        print("warning:", w)

    fused = read_rgb16(a.out)
    s_f = tile_sharpness(fused)
    s_max = np.max(np.stack(per_frame), axis=0)
    keep = s_max > np.percentile(s_max, 20)           # ignore flat tiles
    ratio = s_f[keep] / s_max[keep]
    q = np.percentile(ratio, [5, 25, 50, 75, 95])
    print("fused/max-frame tile sharpness ratio  p5=%.2f p25=%.2f p50=%.2f p75=%.2f p95=%.2f" % tuple(q))

    if a.crops:
        os.makedirs(a.crops, exist_ok=True)
        first = read_rgb16(a.frames[0])
        h, w = fused.shape[:2]
        for name, (cy, cx) in {"center": (0.5, 0.5), "upper": (0.3, 0.5), "lower": (0.7, 0.5)}.items():
            box = (int(h * cy) - 300, int(w * cx) - 450, 600, 900)
            save_crop(os.path.join(a.crops, f"{name}_frame0.jpg"), first, box)
            save_crop(os.path.join(a.crops, f"{name}_fused.jpg"), fused, box)
        print("crops in", a.crops)


if __name__ == "__main__":
    main()
