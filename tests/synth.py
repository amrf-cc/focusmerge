"""Synthetic focus stacks with known ground truth (test-only helpers)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List

import cv2
import numpy as np
import tifffile

FAKE_ICC = b"FOCUSMERGE-TEST-ICC-PROFILE-" * 8


@dataclass
class Stack:
    paths: List[Path]
    truth: np.ndarray            # (H, W, 3) float32, frame-0 geometry, fully sharp, noise-free
    depth: np.ndarray            # (H, W) float32 in [0, 1]
    focus: List[float]
    transforms: List[np.ndarray]  # 3x3 float64: reference coords -> frame-i coords
    shape: tuple                 # (H, W)


def make_scene(h: int, w: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    tex = np.zeros((h, w, 3), np.float32)
    for sigma, amp in ((0.8, 0.30), (2.0, 0.40), (5.0, 0.50), (14.0, 0.60)):
        n = cv2.GaussianBlur(rng.standard_normal((h, w, 3)).astype(np.float32), (0, 0), sigma)
        tex += amp * n / (float(n.std()) + 1e-6)
    tex = 0.5 + 0.10 * tex
    depth = np.tile(np.linspace(0.2, 0.8, w, dtype=np.float32), (h, 1))
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(6):
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        r = int(rng.integers(min(h, w) // 10, min(h, w) // 4))
        m = (xx - cx) ** 2 + (yy - cy) ** 2 < r * r
        depth[m] = float(rng.uniform(0.05, 0.95))
        tex[m] = 0.6 * tex[m] + 0.4 * rng.uniform(0.15, 0.85, 3).astype(np.float32)
    return np.clip(tex, 0.0, 1.0).astype(np.float32), depth


def render_frame(truth, depth, focus, blur_per_depth=4.0, levels=9):
    """Per-pixel Gaussian defocus: sigma = blur_per_depth * |depth - focus|."""
    sigma = blur_per_depth * np.abs(depth - focus)
    top = float(sigma.max()) + 1e-3
    grid = np.linspace(0.0, top, levels)
    layers = np.stack([truth if s == 0 else cv2.GaussianBlur(truth, (0, 0), float(s)) for s in grid])
    pos = np.clip(sigma / (grid[1] - grid[0]), 0, levels - 1 - 1e-4)
    lo = np.floor(pos).astype(np.int64)
    frac = (pos - lo)[..., None].astype(np.float32)
    a = np.take_along_axis(layers, lo[None, ..., None], axis=0)[0]
    b = np.take_along_axis(layers, (lo + 1)[None, ..., None], axis=0)[0]
    return (a * (1 - frac) + b * frac).astype(np.float32)


def make_transform(h, w, scale=1.0, angle_deg=0.0, dx=0.0, dy=0.0):
    c = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    a = math.radians(angle_deg)
    ca, sa = math.cos(a) * scale, math.sin(a) * scale
    r = np.array([[ca, -sa], [sa, ca]])
    m = np.eye(3)
    m[:2, :2] = r
    m[:2, 2] = c - r @ c + np.array([dx, dy])
    return m


def write_tiff(path, rgb, *, compression=None, byteorder="<", rowsperstrip=16, icc=True, tags=True):
    data = np.clip(rgb * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    extra = []
    if icc:
        extra.append((34675, 7, len(FAKE_ICC), FAKE_ICC, True))
    if tags:
        for code, text in ((271, "FUJIFILM"), (272, "X-S20"), (306, "2026:10:04 12:01:56")):
            extra.append((code, 2, len(text) + 1, text, True))
    kw = dict(photometric="rgb", rowsperstrip=rowsperstrip, extratags=extra, metadata=None,
              byteorder=byteorder, software="FocusMerge-test")
    if compression:
        kw.update(compression="zlib", predictor=True)
    tifffile.imwrite(str(path), data, **kw)


def make_stack(directory, n=7, h=384, w=576, seed=0, noise=0.002, jitter=True, names=None, **tiff_kwargs):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed + 1000)
    truth, depth = make_scene(h, w, seed)
    focus = [float(f) for f in np.linspace(0.1, 0.9, n)]
    paths, transforms = [], []
    for i, f in enumerate(focus):
        frame = render_frame(truth, depth, f)
        if jitter and i > 0:
            m = make_transform(
                h, w,
                scale=1.0 + 0.0015 * i + float(rng.normal(0, 0.0003)),
                angle_deg=float(rng.normal(0, 0.05)),
                dx=float(rng.uniform(-2.5, 2.5)), dy=float(rng.uniform(-2.5, 2.5)),
            )
            frame = cv2.warpAffine(frame, m[:2], (w, h),
                                   flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_REPLICATE)
        else:
            m = np.eye(3)
        frame = np.clip(frame + rng.normal(0, noise, frame.shape).astype(np.float32), 0, 1)
        p = directory / (names[i] if names else f"frame_{i:03d}.tif")
        write_tiff(p, frame, **tiff_kwargs)
        paths.append(p)
        # `m` maps frame coords -> reference coords (dst(x) = src(m x) above); the Stack documents
        # reference -> frame, which is what Aligner.matrix reports.
        transforms.append(np.linalg.inv(m))
    return Stack(paths, truth, depth, focus, transforms, (h, w))


def psnr(a, b, border=8):
    if border:
        a = a[border:-border, border:-border]
        b = b[border:-border, border:-border]
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else 10.0 * math.log10(1.0 / mse)


def load_f32(path):
    with tifffile.TiffFile(str(path)) as tf:
        arr = tf.pages[0].asarray()
    return arr.astype(np.float32) / 65535.0


def make_planar_frames(n, h, w, blur_per_depth=12.0, noise=0.004, fine=1.0, seed=0):
    """In-memory stack of a tilted plane: each frame is sharp only in a thin vertical slab, the
    rest strongly defocused, with sensor noise. Mimics a shallow-DOF many-frame stack.
    Returns (truth float32 HxWx3, [frames float32 HxWx3])."""
    truth, _ = make_scene(h, w, seed)
    depth = np.tile(np.linspace(0.05, 0.95, w, dtype=np.float32), (h, 1))
    base = cv2.GaussianBlur(truth, (0, 0), 3.0)
    truth = np.clip(base + fine * (truth - base), 0, 1).astype(np.float32)
    rng = np.random.default_rng(seed + 1)
    frames = []
    for f in np.linspace(0.05, 0.95, n):
        fr = render_frame(truth, depth, float(f), blur_per_depth=blur_per_depth)
        frames.append(np.clip(fr + rng.normal(0, noise, fr.shape).astype(np.float32), 0, 1))
    return truth, frames


def make_deep_stack(directory, n=16, h=320, w=480, blur_per_depth=25.0, noise=0.003, seed=3,
                    bump_at=None, bump=(0.7, 3.9), bump_blur=25):
    """Like a real shallow-DOF stack: neighbours correlate at ~0.99, frames 4+ apart at ~0.9.
    From frame `bump_at` on, the camera is displaced by `bump` px for good (a knock), and that one
    frame also carries horizontal motion blur of `bump_blur` px (the camera was moving)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    truth, frames = make_planar_frames(n, h, w, blur_per_depth=blur_per_depth, noise=noise, seed=seed)
    rng = np.random.default_rng(seed + 50)
    paths, transforms = [], []
    for i, fr in enumerate(frames):
        if i == 0:
            m = np.eye(3)
        else:
            m = make_transform(h, w, scale=1.0 + 0.0008 * i, angle_deg=float(rng.normal(0, 0.03)),
                               dx=float(rng.uniform(-1.0, 1.0)), dy=float(rng.uniform(-1.0, 1.0)))
        if bump_at is not None and i >= bump_at:
            m = m @ make_transform(h, w, dx=bump[0], dy=bump[1])
        out = fr
        if i > 0 or bump_at == 0:
            out = cv2.warpAffine(fr, m[:2], (w, h), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                 borderMode=cv2.BORDER_REPLICATE)
        if bump_at is not None and i == bump_at and bump_blur > 1:
            k = np.full((1, bump_blur), 1.0 / bump_blur, np.float32)
            out = cv2.filter2D(out, -1, k, borderType=cv2.BORDER_REPLICATE)
        p = directory / f"deep_{i:03d}.tif"
        write_tiff(p, np.clip(out, 0, 1))
        paths.append(p)
        transforms.append(np.linalg.inv(m))
    return Stack(paths, truth, np.zeros((h, w), np.float32), [i / max(1, n - 1) for i in range(n)],
                 transforms, (h, w))


def paint_moving_object(path, frame_index, h, w, *, frac_w=0.22, frac_h=0.62, wobble=11.0, seed=0):
    """Draw a hand-held light into a written frame: a sharp, high-contrast, textured box on the left
    edge whose position changes from frame to frame independently of the camera (a hand moves)."""
    rng = np.random.default_rng(seed * 1000 + frame_index)
    img = load_f32(path)
    ox = int(rng.uniform(-wobble, wobble))
    oy = int(rng.uniform(-wobble, wobble))
    x0, x1 = max(0, int(0.0 * w) + ox), int(frac_w * w) + ox
    y0, y1 = int(0.18 * h) + oy, int(0.18 * h + frac_h * h) + oy
    x0, x1, y0, y1 = max(0, x0), min(w, x1), max(0, y0), min(h, y1)
    obj = np.full((y1 - y0, x1 - x0, 3), 0.92, np.float32)
    for k in range(3):                                  # dark bars and a "screen" so ECC can lock on
        yy = (y1 - y0) * (0.15 + 0.28 * k)
        obj[int(yy):int(yy + 0.08 * (y1 - y0)), int(0.1 * (x1 - x0)):int(0.9 * (x1 - x0))] = 0.08
    obj[:, : max(1, (x1 - x0) // 14)] = 0.15
    img[y0:y1, x0:x1] = obj
    write_tiff(path, img)
    return x0, y0, x1, y1
