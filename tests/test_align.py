import numpy as np
import pytest

from fm.align import Aligner, valid_mask
from fm.tiffio import read_rgb16
import fm.align as align_mod
from synth import (make_deep_stack, make_scene, make_stack, make_transform, paint_moving_object,
                   write_tiff)


def corner_error(a, b, shape):
    h, w = shape
    pts = np.array([[0, 0, 1], [w - 1, 0, 1], [0, h - 1, 1], [w - 1, h - 1, 1]], float).T
    return float(np.abs((a @ pts)[:2] - (b @ pts)[:2]).max())


def test_chain_recovers_known_transforms(tmp_path):
    s = make_stack(tmp_path, n=6, h=480, w=720, jitter=True, seed=3)
    al = Aligner(s.shape, work_long_edge=720)
    for p, m_true in zip(s.paths, s.transforms):
        al.register(read_rgb16(str(p)))
        assert corner_error(al.matrix, m_true, s.shape) < 0.6


def test_registered_frames_match_reference_geometry(tmp_path):
    s = make_stack(tmp_path, n=4, h=480, w=720, jitter=True, seed=4, noise=0.0)
    al = Aligner(s.shape, work_long_edge=720)
    ref = None
    for i, p in enumerate(s.paths):
        aligned, mask, warns = al.register(read_rgb16(str(p)))
        a = aligned.astype(np.float32) / 65535.0
        if i == 0:
            ref = a
        else:
            assert mask is not None and mask.shape == s.shape
            inside = mask > 0.5
            # same scene content, different defocus: structure must line up (corr, not exact)
            c = np.corrcoef(a[inside][:, 1], ref[inside][:, 1])[0, 1]
            assert c > 0.95, (i, c)


def test_disabled_returns_input_untouched(tmp_path):
    s = make_stack(tmp_path, n=3, h=120, w=160)
    al = Aligner(s.shape, enabled=False)
    for p in s.paths:
        rgb = read_rgb16(str(p))
        aligned, mask, warns = al.register(rgb)
        assert aligned is rgb and mask is None and warns == []


def test_identical_frames_skip_warp(tmp_path):
    img = make_scene(200, 300, seed=1)[0]
    p = tmp_path / "a.tif"
    write_tiff(p, img)
    al = Aligner((200, 300), work_long_edge=300)
    rgb = read_rgb16(str(p))
    al.register(rgb)
    aligned, mask, warns = al.register(rgb.copy())
    assert mask is None and warns == []
    assert np.array_equal(aligned, rgb)


def test_flat_frame_falls_back_and_chain_continues(tmp_path):
    s = make_stack(tmp_path, n=4, h=300, w=450, jitter=True, seed=7)
    flat = np.full((300, 450, 3), 20000, np.uint16)
    al = Aligner(s.shape, work_long_edge=450)
    al.register(read_rgb16(str(s.paths[0])))
    al.register(read_rgb16(str(s.paths[1])))
    aligned, mask, warns = al.register(flat)            # cannot be registered
    assert aligned.shape == (300, 450, 3)
    assert len(warns) == 1                              # reported once, not once per later frame
    _, _, warns2 = al.register(read_rgb16(str(s.paths[2])))   # chain keeps going, vs the last good frame
    assert warns2 == []
    assert corner_error(al.matrix, s.transforms[2], s.shape) < 0.6


def test_reframed_shot_is_rejected_and_chain_continues(tmp_path):
    s = make_stack(tmp_path, n=4, h=300, w=450, jitter=True, seed=8)
    other = tmp_path / "other.tif"
    write_tiff(other, make_scene(300, 450, seed=99)[0])           # a different composition
    al = Aligner(s.shape, work_long_edge=450)
    al.register(read_rgb16(str(s.paths[0])))
    al.register(read_rgb16(str(s.paths[1])))
    _, _, warns = al.register(read_rgb16(str(other)))
    assert len(warns) == 1
    _, _, warns2 = al.register(read_rgb16(str(s.paths[2])))
    assert warns2 == []
    assert corner_error(al.matrix, s.transforms[2], s.shape) < 0.6


def test_valid_mask_marks_out_of_frame_area():
    m = make_transform(100, 200, dx=10.0)              # frame content shifted: ref x -> src x+10
    mask = valid_mask(m, (100, 200), margin=0)
    assert mask.shape == (100, 200) and mask.dtype == np.float32
    assert mask[50, 5] == 1.0 and mask[50, 190] == 0.0  # right edge of ref has no source pixels
    assert valid_mask(np.eye(3), (100, 200)).min() == 1.0


def test_bump_with_moderate_correlation_is_accepted(tmp_path):
    # Real 22-frame stack: the camera was knocked 3.9 px between frames 11 and 12 and that pair
    # correlated at 0.927, under the old 0.95 gate. A small motion is believable at that level.
    s = make_deep_stack(tmp_path, n=16, h=480, w=720, bump_at=7, bump_blur=55, seed=3)   # cc ~0.88
    al = Aligner(s.shape, work_long_edge=720)
    warnings, errors = [], []
    for p, m in zip(s.paths, s.transforms):
        _, _, w = al.register(read_rgb16(str(p)))
        warnings += w
        errors.append(corner_error(al.matrix, m, s.shape))
    assert warnings == []
    assert errors[7] < 3.0                                # the blurred bump frame itself
    assert max(errors[8:]) < 1.5, [round(e, 2) for e in errors]   # later frames re-anchor cleanly


class _FakeEstimator:
    """Stand-in for ECC: frames carry their index in the pixel value, poses are known, and the
    correlation falls with distance in the stack (as in a shallow-DOF stack: neighbours ~1.0,
    two apart ~0.91, four apart ~0.85)."""

    def __init__(self, shifts, special=None):
        self.shifts = shifts
        self.special = special or {}

    def prepare(self, rgb16, base_hw):
        return np.full(base_hw, float(rgb16[0, 0, 0]), np.float32)

    def estimate(self, prev, cur):
        i, j = int(prev[0, 0]), int(cur[0, 0])
        (dx0, dy0), (dx1, dy1) = self.shifts[i], self.shifts[j]
        w = np.array([[1, 0, dx1 - dx0], [0, 1, dy1 - dy0], [0, 0, 1.0]])
        key = (min(i, j), max(i, j))
        cc = self.special.get(key, 0.9996 if abs(i - j) == 1 else max(0.6, 0.97 - 0.03 * abs(i - j)))
        return w, cc


def _run_fake(monkeypatch, fake, n=22):
    shape = (2080, 3120)                                  # "small motion" is 1 % of the long edge
    monkeypatch.setattr(align_mod, "_prepare", fake.prepare)
    monkeypatch.setattr(align_mod, "_estimate", fake.estimate)
    al = Aligner(shape, work_long_edge=max(shape))
    warnings, errors = [], []
    for i in range(n):
        frame = np.full((*shape, 3), i, np.uint16)
        _, _, w = al.register(frame)
        warnings += w
        want = np.eye(3)
        want[:2, 2] = fake.shifts[i]
        errors.append(corner_error(al.matrix, want, shape))
    return warnings, errors


def _shifts_with_bump(n=22, at=11, bump=(0.7, 3.9)):
    out = []
    for i in range(n):
        drift = (0.15 * i, 0.1 * i)
        out.append((drift[0] + (bump[0] if i >= at else 0), drift[1] + (bump[1] if i >= at else 0)))
    return out


def test_rejected_hop_does_not_make_every_later_frame_fail(monkeypatch):
    # The failure seen on 22 real frames: one rejected hop (frame 12), then every later frame was
    # compared with the last good one, fell below the gate as the focus moved on, and failed too.
    fake = _FakeEstimator(_shifts_with_bump(), special={(10, 11): 0.70, (10, 12): 0.60})
    warnings, errors = _run_fake(monkeypatch, fake)
    assert len(warnings) == 1, warnings                   # only the frame that really had no match
    assert max(errors[12:]) <= 4.0 + 1e-6                 # bounded by the one lost hop, not growing


def test_bump_is_recovered_when_a_later_frame_matches_the_last_good_one(monkeypatch):
    # Frame 12's own hop is unusable, but frame 13 (two apart from frame 11) still matches it at 0.85
    # with a small motion: its geometry, and so the bump, must be recovered exactly.
    fake = _FakeEstimator(_shifts_with_bump(), special={(10, 11): 0.70, (10, 12): 0.85})
    warnings, errors = _run_fake(monkeypatch, fake)
    assert len(warnings) == 1, warnings
    assert max(errors[12:]) < 1e-6, [round(e, 3) for e in errors]


def _error_at(est, true, pts):
    p = np.array([[x, y, 1.0] for x, y in pts]).T
    return float(np.abs((est @ p)[:2] - (true @ p)[:2]).max())


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_moving_hand_held_object_does_not_bias_alignment(tmp_path, seed):
    # A hand-held light in frame moves independently of the camera. Real frames 1196 -> 1197: the
    # whole-frame fit was bent by it (+8 px at the light, -6 px at the far table) while the static
    # scene had moved < 1 px. The static scene must set the alignment.
    h, w = 480, 720
    s = make_stack(tmp_path, n=6, h=h, w=w, jitter=True, seed=seed)
    for i, p in enumerate(s.paths):
        paint_moving_object(p, i, h, w, seed=seed)
    al = Aligner(s.shape, work_long_edge=720)
    away_from_object = [(0.45 * w, 0.3 * h), (0.6 * w, 0.5 * h), (0.85 * w, 0.7 * h), (0.95 * w, 0.2 * h)]
    warnings, errors = [], []
    for p, m in zip(s.paths, s.transforms):
        _, _, wr = al.register(read_rgb16(str(p)))
        warnings += wr
        errors.append(_error_at(al.matrix, m, away_from_object))
    assert warnings == []
    assert max(errors) < 1.0, [round(e, 2) for e in errors]


def test_object_filling_most_of_the_frame_falls_back_without_error(tmp_path):
    h, w = 300, 450
    s = make_stack(tmp_path, n=3, h=h, w=w, jitter=True, seed=4)
    for i, p in enumerate(s.paths):
        paint_moving_object(p, i, h, w, frac_w=0.85, frac_h=0.9, seed=4)
    al = Aligner(s.shape, work_long_edge=450)
    for p in s.paths:
        al.register(read_rgb16(str(p)))
        assert np.isfinite(al.matrix).all()


def _relight(path, frame_index, h, w, seed):
    """Add a pool of light somewhere different in every frame (a hand-held light changes the
    exposure of parts of the static scene, it does not move the scene)."""
    from synth import load_f32
    rng = np.random.default_rng(seed * 77 + frame_index)
    img = load_f32(path)
    cy, cx = rng.uniform(0.2, 0.8) * h, rng.uniform(0.1, 0.9) * w
    sigma = rng.uniform(0.12, 0.25) * w
    yy, xx = np.mgrid[0:h, 0:w]
    pool = np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sigma ** 2)))[..., None].astype(np.float32)
    gain = rng.uniform(0.6, 1.4)
    write_tiff(path, np.clip(img * (1.0 + gain * pool), 0.0, 1.0).astype(np.float32))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_changing_illumination_does_not_move_the_alignment(tmp_path, seed):
    # The user layers exposures from hand-held-light shots over the original, so the scene must be
    # registered to the scene, not to where the light happens to fall.
    h, w = 480, 720
    s = make_stack(tmp_path, n=6, h=h, w=w, jitter=True, seed=seed)
    for i, p in enumerate(s.paths):
        _relight(p, i, h, w, seed)
    al = Aligner(s.shape, work_long_edge=720)
    pts = [(0.2 * w, 0.3 * h), (0.5 * w, 0.5 * h), (0.8 * w, 0.7 * h), (0.95 * w, 0.1 * h)]
    warnings, errors = [], []
    for p, m in zip(s.paths, s.transforms):
        _, _, wr = al.register(read_rgb16(str(p)))
        warnings += wr
        errors.append(_error_at(al.matrix, m, pts))
    assert warnings == []
    assert max(errors) < 1.0, [round(e, 2) for e in errors]
