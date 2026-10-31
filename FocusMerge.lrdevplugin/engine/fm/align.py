"""Frame-to-frame registration (affine, chained) and warping to frame-0 geometry."""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

# (scale relative to the prepared base image, ECC max iterations): coarse to fine.
_ECC_LEVELS = ((0.25, 40), (0.5, 25), (1.0, 12))
_ECC_LEVELS_REFIT = ((0.5, 15), (1.0, 8))      # refits start from a good estimate
_MAX_SCALE_DEV = 0.05
_MAX_ROT_DEG = 5.0
_MAX_SHIFT_FRAC = 0.10
_SKIP_WARP_PX = 0.05
# Moving objects (a hand-held light, a hand) bend a whole-frame fit: on real frames the fit was +8 px
# off at the light and -6 px at the far table while the static scene had moved < 1 px. After a first
# fit, regions that disagree with it (compared low-passed, so defocus differences do not count) are
# left out and the frame is fitted again on the rest.
_OUTLIER_SIGMA_FRAC = 0.0025   # low-pass sigma as a fraction of the long edge (>= 1.5 px)
_OUTLIER_MIN = 0.15            # residual floor, log-intensity units (~16 % brightness)
_OUTLIER_K = 6.0               # ... or this many times the median residual,
_OUTLIER_CAP = 0.5             # but never above this (a garbage fit has a huge median)
_OUTLIER_MIN_FRAC = 0.015      # below this share of the frame there is nothing worth refitting
_OUTLIER_MAX_FRAC = 0.60       # above it we cannot tell a moving object from the scene: keep the plain fit
_FAST_PATH_CORRELATION = 0.97  # a plain fit at or above this, with no outliers, is taken as is
# A strong moving edge can pull the plain fit (1-2 px) without leaving outliers or lowering the
# correlation: the fit matches the object and the low-contrast static texture is hidden by the
# low-pass. The tell is disagreement with a texture-balanced fit: a normalised refinement started
# from the plain fit moves a clean hop by <= 0.12 px (real stacks, base resolution) and a biased one
# by 2-16 px.
_BIAS_CHECK_PX = 0.25
# Where the scene is lit differently from frame to frame (a hand-held light) raw intensities do not
# match even when perfectly registered: plain ECC then returns nonsense (100-300 px off, and its
# residuals are huge so a relative outlier test passes it). Everything that judges a fit therefore
# works on a log-intensity high-pass: invariant to smooth multiplicative lighting changes.
_INV_BIG_FRAC = 0.02           # high-pass scale, as a fraction of the long edge
_INV_EPS = 0.02                # keeps the log finite in the shadows
_REFIT_PASSES = 2
# A high-contrast moving object has ~16x the contrast of the scene's fine texture and, in raw ECC,
# ~256x the weight, so the first fit is registered to it. Registering on locally contrast-normalised
# images gives every texture the same say; measured on a synthetic hand-held light: 2-10 px error
# became 0.01-0.1 px.
_NORM_SIGMA_FRAC = 0.006       # window of the local mean/contrast, as a fraction of the long edge
_NORM_EPS = 0.02               # keeps flat, noisy areas from being amplified
_REFIT_MAX_CHANGE_FRAC = 0.05  # a refit that moves the corners by more than this (of the long edge) is distrusted
# Neighbouring frames of a real stack correlate at >= 0.999 (11-frame X-S20 stack); re-framed or
# blank shots score <= 0.9 even when ECC "converges" to a plausible-looking warp. But a camera
# knock between two frames scored 0.927 on a real 22-frame stack, so the bar depends on how far the
# frame moved: a small, believable motion needs only moderate similarity, a large one must look like
# the same picture.
_MIN_CORRELATION = 0.95
_MIN_CORRELATION_SMALL = 0.80
_CONFIDENT_CORRELATION = 0.95   # a hop at or above this is trusted as an anchor for later frames
_SMALL_SHIFT_FRAC = 0.01     # of the long edge
_SMALL_SCALE_DEV = 0.01
_SMALL_ROT_DEG = 1.0


def _level_matrix(src_hw: Tuple[int, int], dst_hw: Tuple[int, int]) -> np.ndarray:
    """3x3 map from pixel-centre coords of an image of size src_hw to its resized version dst_hw."""
    sy, sx = dst_hw[0] / src_hw[0], dst_hw[1] / src_hw[1]
    return np.array([[sx, 0, (sx - 1) / 2], [0, sy, (sy - 1) / 2], [0, 0, 1]], np.float64)


def _prepare(rgb16: np.ndarray, base_hw: Tuple[int, int]) -> np.ndarray:
    gray = cv2.cvtColor(rgb16, cv2.COLOR_RGB2GRAY)
    if gray.shape != base_hw:
        gray = cv2.resize(gray, (base_hw[1], base_hw[0]), interpolation=cv2.INTER_AREA)
    g = gray.astype(np.float32) * (1.0 / 65535.0)
    return cv2.GaussianBlur(g, (0, 0), 1.0)


def _ecc_pyramid(prev: np.ndarray, cur: np.ndarray, init: np.ndarray,
                 mask_cur: Optional[np.ndarray] = None,
                 levels=_ECC_LEVELS) -> Tuple[Optional[np.ndarray], float]:
    """Coarse-to-fine ECC from `init` (3x3, base resolution). `mask_cur` (uint8, 255 = use) is in the
    coordinates of `cur`, the image being warped. Returns (3x3 or None, correlation)."""
    base_hw = prev.shape
    warp_base = init
    cc = 0.0
    for level, (scale, iters) in enumerate(levels):
        hw = (max(16, round(base_hw[0] * scale)), max(16, round(base_hw[1] * scale)))
        if hw == base_hw:
            a, b, m = prev, cur, mask_cur
        else:
            a = cv2.resize(prev, (hw[1], hw[0]), interpolation=cv2.INTER_AREA)
            b = cv2.resize(cur, (hw[1], hw[0]), interpolation=cv2.INTER_AREA)
            m = None if mask_cur is None else cv2.resize(mask_cur, (hw[1], hw[0]), interpolation=cv2.INTER_NEAREST)
        s = _level_matrix(base_hw, hw)
        w_level = (s @ warp_base @ np.linalg.inv(s))[:2].astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-5)
        try:
            cc, w_level = cv2.findTransformECC(a, b, w_level, cv2.MOTION_AFFINE, criteria, m, 5)
        except cv2.error:
            return (None, 0.0) if level == 0 else (warp_base, cc)   # keep the coarser estimate
        full = np.vstack([w_level.astype(np.float64), [0.0, 0.0, 1.0]])
        warp_base = np.linalg.inv(s) @ full @ s
    return warp_base, float(cc)


def _invariant(g: np.ndarray) -> np.ndarray:
    """Log-intensity high-pass (float32): smooth lighting changes drop out, structure stays."""
    lg = np.log(g + _INV_EPS)
    return lg - cv2.GaussianBlur(lg, (0, 0), max(4.0, _INV_BIG_FRAC * max(g.shape)))


def _normalise(g: np.ndarray) -> np.ndarray:
    """Local mean removed and local contrast equalised (float32, same shape)."""
    sigma = max(2.0, _NORM_SIGMA_FRAC * max(g.shape))
    d = g - cv2.GaussianBlur(g, (0, 0), sigma)
    sd = np.sqrt(cv2.GaussianBlur(d * d, (0, 0), sigma))
    return d / (sd + _NORM_EPS)


def _warped(cur: np.ndarray, warp: np.ndarray) -> np.ndarray:
    h, w = cur.shape
    return cv2.warpAffine(cur, warp[:2], (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def _residual(ph: np.ndarray, ch: np.ndarray, warp: np.ndarray) -> np.ndarray:
    """|low-passed prev - low-passed warped cur| in invariant space (low-passed so that the
    difference in defocus between neighbouring frames does not count)."""
    sigma = max(1.5, _OUTLIER_SIGMA_FRAC * max(ph.shape))
    return np.abs(cv2.GaussianBlur(ph, (0, 0), sigma) - cv2.GaussianBlur(_warped(ch, warp), (0, 0), sigma))


def _outliers(ph: np.ndarray, ch: np.ndarray, warp: np.ndarray) -> np.ndarray:
    """bool (base size, in prev's coordinates): True where `cur`, warped by `warp`, disagrees with
    `prev` by more than defocus, noise and lighting explain."""
    r = _residual(ph, ch, warp)
    thr = float(np.clip(_OUTLIER_K * float(np.median(r)), _OUTLIER_MIN, _OUTLIER_CAP))
    sigma = max(1.5, _OUTLIER_SIGMA_FRAC * max(ph.shape))
    k = 2 * int(round(3 * sigma)) + 1                       # cover the soft edge of the object
    bad = cv2.dilate((r > thr).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return bad.astype(bool)


def _trimmed_cost(ph: np.ndarray, ch: np.ndarray, warp: np.ndarray, keep: float = 0.7) -> float:
    """How well `warp` explains the scene: mean of the best `keep` share of the residuals, so an
    independently moving object (a minority of pixels) cannot decide it."""
    r = _residual(ph, ch, warp).ravel()
    k = max(1, int(keep * r.size))
    return float(np.partition(r, k - 1)[:k].mean())


def _masked_correlation(prev: np.ndarray, cur: np.ndarray, warp: np.ndarray,
                        excluded: Optional[np.ndarray]) -> float:
    """Zero-mean normalised correlation of prev and warped cur over the pixels that were used
    (inside the frame, not in `excluded`)."""
    h, w = prev.shape
    inside = cv2.warpAffine(np.ones((h, w), np.uint8), warp[:2], (w, h),
                            flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderValue=0).astype(bool)
    if excluded is not None:
        inside &= ~excluded
    if int(inside.sum()) < 64:
        return 0.0
    a = prev[inside].astype(np.float64)
    b = _warped(cur, warp)[inside].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    den = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-12 else 0.0


def _refit(ph: np.ndarray, ch: np.ndarray, src_prev: np.ndarray, src_cur: np.ndarray,
           warp: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """Fit again without the regions that disagree with `warp` (up to _REFIT_PASSES times), in the
    image space (raw or contrast-normalised) the warp came from. Returns (warp, excluded mask)."""
    h, w = ph.shape
    corners = np.array([[0, 0, 1], [w - 1, 0, 1], [0, h - 1, 1], [w - 1, h - 1, 1]], np.float64).T
    excluded: Optional[np.ndarray] = None
    for _ in range(_REFIT_PASSES):
        bad = _outliers(ph, ch, warp)
        frac = float(bad.mean())
        if frac < _OUTLIER_MIN_FRAC or frac > _OUTLIER_MAX_FRAC:
            break
        keep_prev = (~bad).astype(np.uint8) * 255
        keep_cur = cv2.warpAffine(keep_prev, warp[:2], (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        refit, _ = _ecc_pyramid(src_prev, src_cur, warp, keep_cur, _ECC_LEVELS_REFIT)
        if refit is None:
            break
        if float(np.abs((refit @ corners)[:2] - (warp @ corners)[:2]).max()) > _REFIT_MAX_CHANGE_FRAC * max(h, w):
            break
        warp, excluded = refit, bad
    return warp, excluded


def _estimate(prev: np.ndarray, cur: np.ndarray) -> Tuple[Optional[np.ndarray], float]:
    """(3x3 matrix mapping prev-image coords -> cur-image coords at base resolution or None,
    correlation over the pixels that were used).

    Usually one plain ECC fit explains the whole frame. When it does not (low correlation, or part
    of the frame disagrees: a hand-held light or a hand moving on its own, or the lighting changed)
    a contrast-normalised fit is made as well, the one that explains the scene better is kept
    (judged in illumination-invariant space), and it is refitted without the disagreeing regions.
    Normalising wins when a high-contrast object or changing light defeats the plain fit; the plain
    fit wins when a frame is smeared by motion blur (normalising then amplifies the smear)."""
    ph, ch = _invariant(prev), _invariant(cur)
    plain, cc_plain = _ecc_pyramid(prev, cur, np.eye(3))
    pn, cn = _normalise(prev), _normalise(cur)
    h, w = prev.shape
    corners = np.array([[0, 0, 1], [w - 1, 0, 1], [0, h - 1, 1], [w - 1, h - 1, 1]], np.float64).T
    balanced = None                      # normalised fit refined from the plain one
    if plain is not None:
        balanced, _ = _ecc_pyramid(pn, cn, plain, None, _ECC_LEVELS_REFIT)
        unbiased = (balanced is not None and
                    float(np.abs((balanced @ corners)[:2] - (plain @ corners)[:2]).max()) < _BIAS_CHECK_PX)
        if (cc_plain >= _FAST_PATH_CORRELATION and unbiased
                and float(_outliers(ph, ch, plain).mean()) < _OUTLIER_MIN_FRAC):
            return plain, cc_plain
    norm, _ = _ecc_pyramid(pn, cn, np.eye(3))
    normalised = [wp for wp in (balanced, norm) if wp is not None]
    if plain is None and not normalised:
        return None, 0.0
    cost = lambda wp: _trimmed_cost(ph, ch, wp)
    best_norm = min(normalised, key=cost) if normalised else None
    if plain is None:
        warp, space = best_norm, (pn, cn)
    elif best_norm is None:
        warp, space = plain, (prev, cur)
    else:
        c_plain, c_norm = cost(plain), cost(best_norm)
        share = float(_outliers(ph, ch, best_norm).mean())
        if _OUTLIER_MIN_FRAC <= share <= _OUTLIER_MAX_FRAC and c_norm <= 1.02 * c_plain:
            # something moves on its own and the texture-balanced fit explains the scene at least as
            # well as the plain one (which its strong edges bend): use it, minus the moving region
            warp, space = best_norm, (pn, cn)
        elif c_norm < 0.8 * c_plain:
            warp, space = best_norm, (pn, cn)      # lighting changed: plain intensities do not match
        else:
            warp, space = plain, (prev, cur)       # plain fit, e.g. a smeared frame (normalising hurts)
    warp, excluded = _refit(ph, ch, space[0], space[1], warp)
    return warp, max(_masked_correlation(prev, cur, warp, excluded),
                     _masked_correlation(ph, ch, warp, excluded))


def _motion(w: np.ndarray, hw: Tuple[int, int]) -> Optional[Tuple[float, float, float]]:
    """(scale, rotation in degrees, displacement of the frame centre in px), None if degenerate."""
    if not np.isfinite(w).all():
        return None
    a = w[:2, :2]
    det = float(np.linalg.det(a))
    if det <= 0:
        return None
    h, wd = hw
    c = np.array([(wd - 1) / 2.0, (h - 1) / 2.0, 1.0])
    d = w @ c - c
    return (math.sqrt(det), math.degrees(math.atan2(a[1, 0] - a[0, 1], a[0, 0] + a[1, 1])),
            math.hypot(d[0], d[1]))


def _plausible(w: np.ndarray, hw: Tuple[int, int]) -> bool:
    m = _motion(w, hw)
    if m is None:
        return False
    scale, rot, shift = m
    return (abs(scale - 1) <= _MAX_SCALE_DEV and abs(rot) <= _MAX_ROT_DEG
            and shift <= _MAX_SHIFT_FRAC * max(hw))


def _accepted(w: Optional[np.ndarray], cc: float, hw: Tuple[int, int]) -> bool:
    """A registration is believed when the transform is plausible and the pictures are similar
    enough for how far it moved."""
    if w is None or not _plausible(w, hw):
        return False
    scale, rot, shift = _motion(w, hw)
    small = (shift <= _SMALL_SHIFT_FRAC * max(hw) and abs(scale - 1) <= _SMALL_SCALE_DEV
             and abs(rot) <= _SMALL_ROT_DEG)
    return cc >= (_MIN_CORRELATION_SMALL if small else _MIN_CORRELATION)


def _max_corner_shift(m: np.ndarray, hw: Tuple[int, int]) -> float:
    h, w = hw
    pts = np.array([[0, 0, 1], [w - 1, 0, 1], [0, h - 1, 1], [w - 1, h - 1, 1]], np.float64).T
    return float(np.abs((m @ pts)[:2] - pts[:2]).max())


def valid_mask(m: np.ndarray, shape_hw: Tuple[int, int], margin: int = 2) -> np.ndarray:
    """float32 (H, W): 1 where the reference pixel maps to a pixel inside the source frame."""
    h, w = shape_hw
    corners = np.array([[0, 0, 1], [w - 1, 0, 1], [w - 1, h - 1, 1], [0, h - 1, 1]], np.float64).T
    ref = (np.linalg.inv(m) @ corners)[:2].T
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, np.round(ref).astype(np.int32), 255)
    if margin > 0:
        mask = cv2.erode(mask, np.ones((2 * margin + 1, 2 * margin + 1), np.uint8))
    return mask.astype(np.float32) * (1.0 / 255.0)


class Aligner:
    """Registers each frame to a neighbour and composes to frame 0's geometry.

    Each registration is either *sure* (similar enough to its reference to be an anchor) or only
    believed (moderate similarity, small motion) or failed. The frame after anything but a sure
    one first tries the last sure frame, which gives it an independent, clean estimate (recovering
    drift a failed hop lost and not inheriting a moderate hop's error), and falls back to the
    previous frame when it is no longer similar enough to that anchor (frames far apart in focus
    differ a lot). So one failure never turns into a run of failures.
    """

    def __init__(self, shape_hw, enabled: bool = True, work_long_edge: int = 1600):
        self.shape = (int(shape_hw[0]), int(shape_hw[1]))
        self.enabled = enabled
        s = min(1.0, work_long_edge / max(self.shape))
        self.base = (max(16, round(self.shape[0] * s)), max(16, round(self.shape[1] * s)))
        self._m = np.eye(3)                       # geometry of the latest frame
        self._prev: Optional[np.ndarray] = None   # latest frame, whatever its status
        self._prev_m = np.eye(3)
        self._prev_sure = True
        self._good: Optional[np.ndarray] = None   # last sure frame (the anchor)
        self._good_m = np.eye(3)
        self._count = 0

    @property
    def matrix(self) -> np.ndarray:
        return self._m.copy()

    def _register_against(self, ref: np.ndarray, cur: np.ndarray) -> Tuple[Optional[np.ndarray], float]:
        w_base, cc = _estimate(ref, cur)
        if w_base is None:
            return None, 0.0
        s = _level_matrix(self.shape, self.base)
        w_full = np.linalg.inv(s) @ w_base @ s
        return (w_full if _accepted(w_full, cc, self.shape) else None), cc

    def register(self, rgb16: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray], List[str]]:
        warnings: List[str] = []
        index = self._count
        self._count += 1
        if not self.enabled:
            return rgb16, None, warnings
        cur = _prepare(rgb16, self.base)
        if self._prev is None:
            self._prev = self._good = cur
            return rgb16, None, warnings

        refs = [(self._prev, self._prev_m, self._prev_sure)]
        if not self._prev_sure:
            refs.insert(0, (self._good, self._good_m, True))
        w, ref_m, sure = None, None, False
        for ref_img, m, ref_sure in refs:
            cand, cc = self._register_against(ref_img, cur)
            if cand is not None:
                w, ref_m, sure = cand, m, ref_sure and cc >= _CONFIDENT_CORRELATION
                break

        if w is None:
            warnings.append(f"frame {index + 1}: alignment failed, assumed the previous frame's geometry")
            m_new = self._prev_m
        else:
            m_new = w @ ref_m
        self._prev, self._prev_m, self._prev_sure, self._m = cur, m_new, sure, m_new
        if sure:
            self._good, self._good_m = cur, m_new

        if _max_corner_shift(self._m, self.shape) < _SKIP_WARP_PX:
            return rgb16, None, warnings
        aligned = cv2.warpAffine(rgb16, self._m[:2], (self.shape[1], self.shape[0]),
                                 flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                 borderMode=cv2.BORDER_REPLICATE)
        return aligned, valid_mask(self._m, self.shape), warnings
