"""Reading frames and writing the merged result (16-bit RGB TIFF)."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional, Tuple

import numpy as np
import tifffile

from .errors import FrameError

_ICC = 34675
_EXIFTOOL_FALLBACKS = ("/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool", "/usr/bin/exiftool")


def read_rgb16(path: str) -> np.ndarray:
    """Return the first page as contiguous uint16 (H, W, 3)."""
    try:
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            arr = page.asarray()
            separate = int(page.planarconfig) == 2
    except Exception as exc:  # tifffile raises many types for bad files
        raise FrameError(f"cannot read {os.path.basename(path)}: {exc}") from exc
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.ndim == 3 and separate:
        arr = np.moveaxis(arr, 0, -1)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise FrameError(f"{os.path.basename(path)}: expected an RGB image, got shape {arr.shape}")
    if arr.dtype == np.uint8:
        arr = arr.astype(np.uint16) * 257
    elif arr.dtype != np.uint16:
        raise FrameError(f"{os.path.basename(path)}: unsupported sample type {arr.dtype}")
    return np.ascontiguousarray(arr[:, :, :3])


def _clone_layout(path: str) -> Optional[Tuple[str, List[Tuple[int, int, int]], Tuple[int, int]]]:
    """(byteorder, [(file_offset, row_start, row_end)], (h, w)) if the file's pixel data can be
    overwritten in place (uncompressed, chunky 16-bit RGB, striped), else None."""
    try:
        with tifffile.TiffFile(path) as tf:
            p = tf.pages[0]
            if not (int(p.compression) == 1 and int(p.planarconfig) == 1 and p.samplesperpixel == 3
                    and p.dtype == np.dtype("uint16") and not p.is_tiled and int(p.predictor) == 1):
                return None
            h, w, rps = p.imagelength, p.imagewidth, p.rowsperstrip
            row_bytes = w * 3 * 2
            spans = []
            for i, (off, cnt) in enumerate(zip(p.dataoffsets, p.databytecounts)):
                r0, r1 = i * rps, min(h, i * rps + rps)
                if r1 <= r0 or (r1 - r0) * row_bytes != cnt:
                    return None
                spans.append((off, r0, r1))
            if not spans or spans[-1][2] != h:
                return None
            return tf.byteorder, spans, (h, w)
    except Exception:
        return None


def _write_clone(template: str, tmp: str, rgb16: np.ndarray, layout) -> bool:
    byteorder, spans, (h, w) = layout
    if rgb16.shape[:2] != (h, w):
        return False
    shutil.copyfile(template, tmp)
    dtype = np.dtype(byteorder + "u2")
    with open(tmp, "r+b") as f:
        for off, r0, r1 in spans:
            f.seek(off)
            f.write(np.ascontiguousarray(rgb16[r0:r1]).astype(dtype, copy=False).tobytes())
    return True


def _write_fallback(template: str, tmp: str, rgb16: np.ndarray) -> None:
    icc = None
    try:
        with tifffile.TiffFile(template) as tf:
            tag = tf.pages[0].tags.get(_ICC)
            if tag is not None:
                icc = bytes(tag.value)
    except Exception:
        icc = None
    extratags = [(_ICC, 7, len(icc), icc, True)] if icc else []
    tifffile.imwrite(tmp, rgb16, photometric="rgb", compression=None, metadata=None,
                     extratags=extratags, software="FocusMerge")


def _copy_metadata_exiftool(template: str, tmp: str) -> bool:
    exe = shutil.which("exiftool") or next((p for p in _EXIFTOOL_FALLBACKS if os.path.exists(p)), None)
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-q", "-overwrite_original", "-TagsFromFile", template, "-all:all", tmp],
                           capture_output=True, timeout=120)
    except Exception:
        return False
    return r.returncode == 0 and os.path.exists(tmp)


def write_result(template: str, out_path: str, rgb16: np.ndarray) -> str:
    """Write the merged image. Prefer cloning the template's container so EXIF/XMP/ICC survive
    byte for byte; otherwise write fresh and copy metadata with exiftool when available.
    Returns "container", "exiftool" or "none"."""
    tmp = out_path + ".part.tif"
    try:
        layout = _clone_layout(template)
        if layout is not None and _write_clone(template, tmp, rgb16, layout):
            how = "container"
        else:
            _write_fallback(template, tmp, rgb16)
            how = "exiftool" if _copy_metadata_exiftool(template, tmp) else "none"
        os.replace(tmp, out_path)
        return how
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise FrameError(f"cannot write {out_path}: {exc}") from exc
