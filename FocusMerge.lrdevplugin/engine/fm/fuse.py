"""Streaming Laplacian-pyramid focus fusion (constant memory in the number of frames).

Each frame gets one focus map (local Laplacian energy of its luminance, raised to a power). The
Gaussian pyramid of that map weights every Laplacian band of the frame, so all bands in a region
come from the same frames (no per-band disagreement) while seams stay smooth at coarse scales.

Tuned on synthetic ground truth (tests/test_fuse.py, PSNR vs the sharp scene; the single-frame
oracle ceiling there is ~54 dB): per-band energy weights reached 46-49 dB, this design 50.0-50.6.

The weight exponent matters more as the frame count grows: every out-of-focus frame keeps a small
weight, and 10-45 of them add up and soften the one sharp frame. On the real 11-frame X-S20 stack
p=8 left the sharpest regions at median 0.83 of the best frame's detail, p=16 at 0.98.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

_K = 0.02             # typical edge energy; normalises the focus map so high powers stay in range
_FLOOR = 0.05         # normalised floor: weights stay >0 in flat areas (0.05**16 ~ 1.5e-21)
# base**p is accumulated over up to ~64 frames, and pyrDown sums 256 taps before normalising, so
# the base is clamped such that base**p <= FLT_MAX / (256 * 64). At p=16 that is 139 (never reached:
# real frames peak near 57), at p=24 it is 27, i.e. only the very hardest edges saturate (equal
# weights there instead of the NaN -> black pixels an unclamped power gave).
_FLT_MAX = float(np.finfo(np.float32).max)
_HEADROOM = 256.0 * 64.0
_PRE_BLUR = 0.7       # sigma applied to luminance before the Laplacian (noise robustness)
_ENERGY_BLUR = 2.0    # sigma smoothing the |Laplacian| into a local energy
_EXPONENTS = {1: 8, 2: 16, 3: 24}  # sharpness -> weight exponent p (default 16)
_MAX_LEVELS = 7


def pyramid_levels(h: int, w: int) -> int:
    return int(max(2, min(_MAX_LEVELS, math.floor(math.log2(min(h, w))) - 4)))


def _power(e: np.ndarray, p: int) -> np.ndarray:
    """e ** p for an integer p >= 1 by repeated squaring (float32 multiplies, no exp/log)."""
    result = None
    base = e
    while p:
        if p & 1:
            result = base if result is None else cv2.multiply(result, base)
        p >>= 1
        if p:
            base = cv2.multiply(base, base)
    return result


class PyramidFuser:
    """Accumulate frames; the result is the weighted average of their Laplacian pyramids."""

    def __init__(self, shape_hw: Tuple[int, int], sharpness: int = 2):
        h, w = int(shape_hw[0]), int(shape_hw[1])
        self.levels = pyramid_levels(h, w)
        self.exponent = _EXPONENTS[sharpness]
        self._cap = float((_FLT_MAX / _HEADROOM) ** (1.0 / self.exponent))
        self.shapes: List[Tuple[int, int]] = []
        hh, ww = h, w
        for _ in range(self.levels + 1):
            self.shapes.append((hh, ww))
            hh, ww = (hh + 1) // 2, (ww + 1) // 2
        # acc[l] / wsum[l]: band l (0 = finest); the last entry is the low-pass residual.
        self.acc = [np.zeros((s[0], s[1], 3), np.float32) for s in self.shapes]
        self.wsum = [np.zeros(s, np.float32) for s in self.shapes]
        self.count = 0

    def _focus_map(self, img: np.ndarray, valid: Optional[np.ndarray]) -> np.ndarray:
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        g = cv2.GaussianBlur(g, (0, 0), _PRE_BLUR)
        e = np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3))
        e = cv2.GaussianBlur(e, (0, 0), _ENERGY_BLUR)
        e *= 1.0 / _K
        e += _FLOOR
        np.minimum(e, self._cap, out=e)
        e = _power(e, self.exponent)
        if valid is not None:
            cv2.multiply(e, valid, dst=e)
        return e

    def add(self, img: np.ndarray, valid: Optional[np.ndarray] = None) -> None:
        """img: float32 (H, W, 3) in [0, 1]; valid: float32 (H, W) in [0, 1] or None."""
        n = self.levels
        gw = [self._focus_map(img, valid)]
        gp = [img]
        for _ in range(n):
            gw.append(cv2.pyrDown(gw[-1]))
            gp.append(cv2.pyrDown(gp[-1]))
        for l in range(n):
            hh, ww = self.shapes[l]
            band = cv2.subtract(gp[l], cv2.pyrUp(gp[l + 1], dstsize=(ww, hh)))
            w3 = cv2.merge((gw[l], gw[l], gw[l]))
            cv2.multiply(band, w3, dst=w3)
            cv2.add(self.acc[l], w3, dst=self.acc[l])
            cv2.add(self.wsum[l], gw[l], dst=self.wsum[l])
        w3 = cv2.merge((gw[n], gw[n], gw[n]))
        cv2.multiply(gp[n], w3, dst=w3)
        cv2.add(self.acc[n], w3, dst=self.acc[n])
        cv2.add(self.wsum[n], gw[n], dst=self.wsum[n])
        self.count += 1

    def result(self) -> np.ndarray:
        out = None
        for l in range(self.levels, -1, -1):
            band = self.acc[l] / np.maximum(self.wsum[l], 1e-37)[..., None]
            if out is None:
                out = band
            else:
                hh, ww = self.shapes[l]
                out = cv2.pyrUp(out, dstsize=(ww, hh)) + band
        return np.clip(out, 0.0, 1.0, out=out)
