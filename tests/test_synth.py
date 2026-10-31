import numpy as np

from synth import FAKE_ICC, load_f32, make_stack, psnr


def test_stack_shapes_and_truth_geometry(tmp_path):
    s = make_stack(tmp_path, n=5, h=240, w=320)
    assert len(s.paths) == 5 and all(p.exists() for p in s.paths)
    assert s.truth.shape == (240, 320, 3) and s.depth.shape == (240, 320)
    assert np.allclose(s.transforms[0], np.eye(3))
    assert not np.allclose(s.transforms[1], np.eye(3))
    f0 = load_f32(s.paths[0])
    assert f0.shape == (240, 320, 3)
    assert 20.0 < psnr(f0, s.truth) < 60.0  # partly defocused, but the same scene


def test_deterministic(tmp_path):
    a = make_stack(tmp_path / "a", n=3, h=120, w=160, seed=5)
    b = make_stack(tmp_path / "b", n=3, h=120, w=160, seed=5)
    assert np.array_equal(load_f32(a.paths[2]), load_f32(b.paths[2]))


def test_tiff_options(tmp_path):
    s = make_stack(tmp_path, n=2, h=64, w=96, compression="zlib", byteorder=">")
    import tifffile
    with tifffile.TiffFile(s.paths[0]) as tf:
        p = tf.pages[0]
        assert tf.byteorder == ">" and int(p.compression) != 1
        assert bytes(p.tags[34675].value) == FAKE_ICC
