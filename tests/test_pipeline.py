import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from fm.errors import Cancelled, FocusMergeError, FrameError, NotEnoughFrames
from fm.frames_queue import follow_frames, static_frames
from fm.pipeline import MergeOptions, run_merge
from synth import load_f32, make_stack, psnr, write_tiff


def merge(paths, out, **kw):
    return run_merge(static_frames([str(p) for p in paths]), MergeOptions(out_path=str(out), **kw))


def test_end_to_end_zip_inputs_with_handheld_jitter(tmp_path):
    s = make_stack(tmp_path / "in", n=7, h=360, w=540, jitter=True, compression="zlib")
    res = merge(s.paths, tmp_path / "out.tif")
    out = load_f32(tmp_path / "out.tif")
    assert out.shape == s.truth.shape
    assert psnr(out, s.truth) >= psnr(load_f32(s.paths[0]), s.truth) + 3.0
    assert res.frames == 7 and res.aligned and res.seconds > 0
    assert res.metadata in ("exiftool", "none")          # ZIP template cannot be cloned
    assert res.peak_rss_mb > 0


def test_uncompressed_inputs_clone_the_container(tmp_path):
    s = make_stack(tmp_path / "in", n=4, h=240, w=320)
    res = merge(s.paths, tmp_path / "out.tif")
    assert res.metadata == "container"


def test_follow_mode_with_slow_writer_and_progress_file(tmp_path):
    s = make_stack(tmp_path / "in", n=5, h=240, w=320)
    q, prog = tmp_path / "queue.txt", tmp_path / "progress.txt"
    q.write_text("")

    def feed():
        for p in s.paths:
            time.sleep(0.15)
            with open(q, "a") as f:
                f.write(str(p) + "\n")
        with open(q, "a") as f:
            f.write("#END\n")

    t = threading.Thread(target=feed)
    t.start()
    opts = MergeOptions(out_path=str(tmp_path / "out.tif"), expected=5, progress_file=str(prog),
                        result_file=str(tmp_path / "result.json"))
    res = run_merge(follow_frames(str(q), poll=0.02, idle_timeout=10), opts)
    t.join()
    assert res.frames == 5
    done, total, stage, _msg = prog.read_text().split("|", 3)
    assert (done, total, stage) == ("5", "5", "done")
    data = json.loads((tmp_path / "result.json").read_text())
    assert data["frames"] == 5 and data["output"].endswith("out.tif") and data["warnings"] == []
    # The engine waited for the slow writer (Lightroom rendering); the log must not blame the engine.
    assert res.timings["wait"] >= 0.5
    assert data["wait_seconds"] >= 0.5 and data["busy_seconds"] <= data["seconds"]


def test_abort_leaves_no_output(tmp_path):
    s = make_stack(tmp_path / "in", n=3, h=120, w=160)
    q = tmp_path / "queue.txt"
    q.write_text("".join(f"{p}\n" for p in s.paths[:2]) + "#ABORT\n")
    out = tmp_path / "out.tif"
    with pytest.raises(Cancelled):
        run_merge(follow_frames(str(q), poll=0.01), MergeOptions(out_path=str(out)))
    assert not out.exists() and not list(tmp_path.glob("*.part.tif"))


def test_single_frame_is_not_enough(tmp_path):
    s = make_stack(tmp_path / "in", n=2, h=96, w=128)
    with pytest.raises(NotEnoughFrames):
        merge(s.paths[:1], tmp_path / "out.tif")


def test_mismatched_dimensions_fail_cleanly(tmp_path):
    a = make_stack(tmp_path / "a", n=2, h=96, w=128)
    b = make_stack(tmp_path / "b", n=2, h=96, w=130)
    out = tmp_path / "out.tif"
    with pytest.raises(FrameError, match="dimensions"):
        merge([a.paths[0], b.paths[1]], out)
    assert not out.exists() and not list(tmp_path.glob("*.part.tif"))


def test_unwritable_output_dir_fails_cleanly(tmp_path):
    s = make_stack(tmp_path / "in", n=2, h=96, w=128)
    with pytest.raises(FrameError):
        merge(s.paths, tmp_path / "missing-dir" / "out.tif")


def test_paths_with_spaces_quotes_unicode(tmp_path):
    folder = tmp_path / "External Drive" / "Focus Stacks" / "o'brien café ☕"
    names = [f"_DSF{1399 + i}-{i + 2}.tif" for i in range(3)]
    s = make_stack(folder, n=3, h=120, w=160, names=names)
    out = folder / "out put.tif"
    assert merge(s.paths, out).frames == 3 and out.exists()


def test_flat_frame_in_stack_completes_with_warning(tmp_path):
    s = make_stack(tmp_path / "in", n=4, h=240, w=320)
    flat = tmp_path / "in" / "flat.tif"
    write_tiff(flat, np.full((240, 320, 3), 0.3, np.float32))
    res = merge([s.paths[0], s.paths[1], flat, s.paths[2], s.paths[3]], tmp_path / "out.tif")
    assert res.frames == 5 and (tmp_path / "out.tif").exists()
    assert len(res.warnings) == 1 and "alignment" in res.warnings[0]


def test_delete_inputs_only_inside_focusmerge_dirs(tmp_path):
    work = tmp_path / "focusmerge-1760000000"
    s = make_stack(work, n=3, h=96, w=128)
    merge(s.paths, tmp_path / "out.tif", delete_inputs=True)
    assert not any(p.exists() for p in s.paths)

    keep = tmp_path / "originals"
    s2 = make_stack(keep, n=3, h=96, w=128)
    res = merge(s2.paths, tmp_path / "out2.tif", delete_inputs=True)
    assert all(p.exists() for p in s2.paths)
    assert any("not deleted" in w for w in res.warnings)


def test_aligned_hook_sees_every_frame(tmp_path):
    s = make_stack(tmp_path / "in", n=3, h=96, w=128)
    seen = []
    merge(s.paths, tmp_path / "out.tif", aligned_hook=lambda i, a: seen.append((i, a.shape)))
    assert seen == [(0, (96, 128, 3)), (1, (96, 128, 3)), (2, (96, 128, 3))]


def test_cancel_file_is_checked_before_writing(tmp_path):
    # Found in review: a cancel pressed while the last frames were merging still left a result.
    s = make_stack(tmp_path / "in", n=3, h=96, w=128)
    cancel = tmp_path / "cancel"
    cancel.write_text("")
    out = tmp_path / "out.tif"
    with pytest.raises(Cancelled):
        merge(s.paths, out, cancel_file=str(cancel))
    assert not out.exists() and not list(tmp_path.glob("*.part.tif"))


def test_non_finite_fusion_is_an_error_not_a_black_image(tmp_path, monkeypatch):
    import fm.pipeline as pipeline

    class Broken(pipeline.PyramidFuser):
        def result(self):
            out = super().result()
            out[10:20, 10:20] = np.nan
            return out

    monkeypatch.setattr(pipeline, "PyramidFuser", Broken)
    s = make_stack(tmp_path / "in", n=2, h=96, w=128)
    out = tmp_path / "out.tif"
    with pytest.raises(FocusMergeError) as e:
        merge(s.paths, out)
    assert e.value.exit_code == 6 and "non-finite" in str(e.value)
    assert not out.exists()
