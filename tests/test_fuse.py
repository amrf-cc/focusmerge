import cv2
import numpy as np
import pytest

from fm.fuse import PyramidFuser, pyramid_levels
from synth import load_f32, make_planar_frames, make_scene, make_stack, psnr


def fuse(frames, sharpness=2, masks=None):
    f = PyramidFuser(frames[0].shape[:2], sharpness)
    for i, fr in enumerate(frames):
        f.add(fr, None if masks is None else masks[i])
    assert f.count == len(frames)
    return f.result()


def pixel_domain_baseline(frames):
    num, den = 0.0, 0.0
    for fr in frames:
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY)
        e = cv2.GaussianBlur(np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3)), (0, 0), 2.0) + 1e-4
        w = e ** 4
        num = num + fr * w[..., None]
        den = den + w
    return num / den[..., None]


def test_levels():
    assert pyramid_levels(4160, 6240) == 7
    assert pyramid_levels(64, 64) == 2
    assert pyramid_levels(16, 16) == 2
    assert pyramid_levels(1024, 1536) == 6


def test_single_frame_reconstructs_exactly():
    img = make_scene(130, 175, seed=2)[0]               # odd sizes on purpose
    out = fuse([img])
    assert out.shape == img.shape and out.dtype == np.float32
    assert psnr(out, img, border=0) > 80


def test_constant_stack_stays_constant():
    frames = [np.full((96, 128, 3), 0.5, np.float32) for _ in range(3)]
    out = fuse(frames)
    assert np.isfinite(out).all() and np.abs(out - 0.5).max() < 1e-4


@pytest.mark.parametrize("sharpness", [1, 2, 3])
def test_fusion_beats_best_frame_and_baseline(tmp_path, sharpness):
    s = make_stack(tmp_path, n=7, h=288, w=432, jitter=False, noise=0.002)
    frames = [load_f32(p) for p in s.paths]
    fused = psnr(fuse(frames, sharpness), s.truth)
    best_single = max(psnr(f, s.truth) for f in frames)
    assert fused >= best_single + 3.0, (fused, best_single)
    if sharpness >= 2:
        # Calibrated on the first measured run (default 50.04 dB, baseline 50.09 dB, ceiling 54 dB):
        # the pyramid must match the pixel-domain baseline within half a dB.
        assert fused >= psnr(pixel_domain_baseline(frames), s.truth) - 0.5, fused


def test_masked_region_is_ignored():
    h, w = 128, 192
    good = make_scene(h, w, seed=3)[0]
    bad = np.random.default_rng(0).random((h, w, 3)).astype(np.float32)   # very high energy
    mask = np.ones((h, w), np.float32)
    mask[:, : w // 2] = 0.0
    out = fuse([good, bad], masks=[None, mask])
    # The coarse pyramid levels smooth the mask edge over ~2^levels px (measured: 41 dB at 8 px,
    # 61 dB at 16 px, exact beyond 32 px), so masked content must not show up from 32 px in.
    assert psnr(out[:, : w // 2 - 32], good[:, : w // 2 - 32], border=0) > 60
    assert np.isfinite(out).all()


def test_many_frames_do_not_soften_the_sharp_one():
    # Found on real 11-frame stacks: with soft weights the many out-of-focus frames add up and
    # blur the one sharp frame. 45 frames, thin sharp slabs, ISO-1250-like noise.
    # Measured: p=8 -> 47.6 dB, p=16 -> 49.2 dB (best single frame ~30 dB).
    truth, frames = make_planar_frames(45, 240, 360, blur_per_depth=12.0, noise=0.004)
    assert psnr(fuse(frames), truth) >= 48.5


@pytest.mark.parametrize("sharpness", [1, 2, 3])
def test_high_exponents_stay_finite_in_flat_areas(sharpness):
    rng = np.random.default_rng(3)
    frames = [np.clip(0.5 + rng.normal(0, 0.0005, (96, 128, 3)), 0, 1).astype(np.float32) for _ in range(45)]
    out = fuse(frames, sharpness)
    assert np.isfinite(out).all() and np.abs(out - 0.5).max() < 0.01


@pytest.mark.parametrize("sharpness", [1, 2, 3])
def test_full_contrast_edges_stay_finite(sharpness):
    # Found in review: at p=24 a black/white edge overflowed float32 -> NaN -> black holes.
    img = np.zeros((128, 192, 3), np.float32)
    for k in range(4):
        img[:, k::8] = 1.0                                  # 4-px full-contrast stripes
    out = fuse([img.copy() for _ in range(3)], sharpness)
    assert np.isfinite(out).all()
    assert psnr(out, img, border=0) > 60                   # identical frames reconstruct the input


@pytest.mark.parametrize("sharpness", [1, 2, 3])
def test_binary_noise_stays_finite_with_many_frames(sharpness):
    rng = np.random.default_rng(5)
    frames = [(rng.random((96, 128, 3)) > 0.5).astype(np.float32) for _ in range(45)]
    out = fuse(frames, sharpness)
    assert np.isfinite(out).all()
