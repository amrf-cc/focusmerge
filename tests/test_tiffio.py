import numpy as np
import pytest
import tifffile

from fm import tiffio
from fm.errors import FrameError
from synth import FAKE_ICC, write_tiff


def _img(h=64, w=96, seed=0):
    return np.random.default_rng(seed).random((h, w, 3)).astype(np.float32)


def _snapshot(path):
    with tifffile.TiffFile(str(path)) as tf:
        return {t.code: repr(t.value) for t in tf.pages[0].tags.values() if t.code not in (273, 279)}


@pytest.mark.parametrize("compression", [None, "zlib"])
@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_read_rgb16(tmp_path, compression, byteorder):
    img = _img()
    p = tmp_path / "a.tif"
    write_tiff(p, img, compression=compression, byteorder=byteorder)
    out = tiffio.read_rgb16(str(p))
    assert out.dtype == np.uint16 and out.shape == (64, 96, 3) and out.flags["C_CONTIGUOUS"]
    assert np.abs(out.astype(np.float32) / 65535 - img).max() < 1.5 / 65535


def test_read_promotes_8bit(tmp_path):
    p = tmp_path / "b.tif"
    arr = (np.arange(64 * 96 * 3) % 256).astype(np.uint8).reshape(64, 96, 3)
    tifffile.imwrite(str(p), arr, photometric="rgb")
    out = tiffio.read_rgb16(str(p))
    assert out.dtype == np.uint16 and out.max() == 255 * 257


def test_read_drops_alpha(tmp_path):
    p = tmp_path / "c.tif"
    arr = np.zeros((32, 48, 4), np.uint16)
    arr[..., 0] = 1000
    arr[..., 3] = 65535
    tifffile.imwrite(str(p), arr, photometric="rgb", extrasamples=(2,))
    out = tiffio.read_rgb16(str(p))
    assert out.shape == (32, 48, 3) and int(out[0, 0, 0]) == 1000


def test_read_gray_becomes_rgb(tmp_path):
    p = tmp_path / "g.tif"
    tifffile.imwrite(str(p), np.full((20, 30), 4000, np.uint16))
    out = tiffio.read_rgb16(str(p))
    assert out.shape == (20, 30, 3) and (out == 4000).all()


def test_read_garbage_raises(tmp_path):
    p = tmp_path / "bad.tif"
    p.write_bytes(b"this is not a tiff")
    with pytest.raises(FrameError):
        tiffio.read_rgb16(str(p))


@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_write_result_clones_container(tmp_path, byteorder):
    src = tmp_path / "frame0.tif"
    write_tiff(src, _img(130, 70), byteorder=byteorder, rowsperstrip=16)  # last strip is short
    new = (np.random.default_rng(1).random((130, 70, 3)) * 65535).astype(np.uint16)
    out = tmp_path / "out.tif"
    assert tiffio.write_result(str(src), str(out), new) == "container"
    assert out.stat().st_size == src.stat().st_size
    assert np.array_equal(tiffio.read_rgb16(str(out)), new)
    assert _snapshot(out) == _snapshot(src)
    with tifffile.TiffFile(str(out)) as tf:
        assert tf.byteorder == byteorder
    assert not (tmp_path / "out.tif.part.tif").exists()


def test_write_result_falls_back_for_compressed_template(tmp_path):
    src = tmp_path / "zip.tif"
    write_tiff(src, _img(), compression="zlib")
    new = (np.random.default_rng(2).random((64, 96, 3)) * 65535).astype(np.uint16)
    out = tmp_path / "out.tif"
    how = tiffio.write_result(str(src), str(out), new)
    assert how in ("exiftool", "none")
    assert np.array_equal(tiffio.read_rgb16(str(out)), new)
    with tifffile.TiffFile(str(out)) as tf:
        assert bytes(tf.pages[0].tags[34675].value) == FAKE_ICC


def test_write_result_different_size_uses_fallback(tmp_path):
    src = tmp_path / "a.tif"
    write_tiff(src, _img(64, 96))
    new = np.zeros((40, 50, 3), np.uint16)
    out = tmp_path / "out.tif"
    assert tiffio.write_result(str(src), str(out), new) in ("exiftool", "none")
    assert tiffio.read_rgb16(str(out)).shape == (40, 50, 3)


def test_write_result_unwritable_dir(tmp_path):
    src = tmp_path / "a.tif"
    write_tiff(src, _img())
    with pytest.raises(FrameError):
        tiffio.write_result(str(src), str(tmp_path / "nope" / "out.tif"), np.zeros((64, 96, 3), np.uint16))
