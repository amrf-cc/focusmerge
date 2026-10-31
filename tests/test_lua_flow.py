import subprocess
import sys
from pathlib import Path

import numpy as np

from lupa import lua51 as lupa  # Lightroom embeds Lua 5.1

from synth import make_stack, write_tiff

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "FocusMerge.lrdevplugin"


def run_flow(tmp_path, n=4, mutate=None, **mock_overrides):
    lib = tmp_path / "External Drive" / "Focus Stacks"
    s = make_stack(lib, n=n, h=240, w=320, names=[f"_DSF{1399 + i}-{i + 2}.tif" for i in range(n)])
    if mutate:
        mutate(s)
    home = tmp_path / "home"
    venv_bin = home / "Library" / "Application Support" / "FocusMerge" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    # A wrapper, not a symlink: a symlinked venv interpreter would not find its pyvenv.cfg.
    wrapper = venv_bin / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    (home / "Library" / "Logs" / "FocusMerge").mkdir(parents=True)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    # photos handed to the plugin out of capture order on purpose
    photos = [{"path": str(p), "time": 100 + i} for i, p in enumerate(s.paths)][::-1]
    cfg = {"mockPath": str(ROOT / "tests/lua/mock_sdk.lua"), "home": str(home), "tmp": str(tmp),
           "photos": photos, "renderDelay": 0.02, **mock_overrides}
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)

    def to_lua(v):
        if isinstance(v, dict):
            return lua.table_from({k: to_lua(x) for k, x in v.items()})
        if isinstance(v, list):
            return lua.table_from([to_lua(x) for x in v])
        return v

    g = lua.globals()
    g.MOCK = to_lua(cfg)
    g.PLUGIN_DIR = str(PLUGIN)
    lua.execute(f"dofile('{ROOT / 'tests/lua/run_flow.lua'}')")
    return s, lib, tmp, g.RESULT


def test_flow_merges_imports_and_cleans_up(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=4)
    assert res.error is None, res.error
    out = lib / "_DSF1399-2_FM4.tif"                       # named after the earliest frame
    assert out.exists()
    assert len(res.added) == 1
    added = res.added[1]
    assert added.path == str(out) and added.position == "above"
    assert res.selected is not None
    assert not list(tmp.glob("focusmerge-*"))              # temp folder removed
    assert res.progressDone is True


def test_flow_picks_non_colliding_name(tmp_path):
    lib = tmp_path / "External Drive" / "Focus Stacks"
    lib.mkdir(parents=True)
    (lib / "_DSF1399-2_FM3.tif").write_bytes(b"x")
    s, lib, tmp, res = run_flow(tmp_path, n=3)
    assert res.error is None, res.error
    assert (lib / "_DSF1399-2_FM3-1.tif").exists()


def test_flow_cancel_leaves_nothing(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=5, cancelAfterExports=2)
    assert res.error is None, res.error
    assert not list(lib.glob("*_FM*.tif"))
    assert len(res.added) == 0                              # nothing imported into the catalog
    assert not list(tmp.glob("focusmerge-*"))


def test_flow_render_failure_reports_and_cleans_up(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=4, failRenderAt=3)
    assert res.error is None, res.error
    assert not list(lib.glob("*_FM*.tif"))
    texts = [res.dialogs[i].info for i in range(1, len(res.dialogs) + 1)]
    assert any("mock render failure" in t for t in texts)
    assert not list(tmp.glob("focusmerge-*"))


def test_flow_needs_two_photos(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=4, photos=[{"path": "/x/a.tif", "time": 1}])
    assert len(res.dialogs) == 1 and "two or more" in res.dialogs[1].info


def test_flow_reports_missing_engine(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=3, prefs={"pythonPath": "/nonexistent/python"})
    assert "not installed" in res.dialogs[1].title


def _infos(res):
    return [res.dialogs[i].info for i in range(1, len(res.dialogs) + 1)]


def test_flow_stops_exporting_when_the_engine_dies(tmp_path):
    # Review finding: the export used to run to the end for nothing, piling frames on a full disk.
    def odd_size(s):
        write_tiff(s.paths[2], np.zeros((100, 120, 3), np.float32))

    s, lib, tmp, res = run_flow(tmp_path, n=8, mutate=odd_size, renderDelay=0.5)
    assert res.error is None, res.error
    assert res.exported < 8
    assert any("dimensions" in t for t in _infos(res)), _infos(res)   # the engine's reason is shown
    assert not list(lib.glob("*_FM*.tif")) and not list(tmp.glob("focusmerge-*"))


def test_flow_cleans_up_and_stops_the_engine_after_a_lua_error(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=4, throwOnSession=True)
    assert res.error is not None and "boom" in res.error       # still surfaced to the caller
    assert not list(tmp.glob("focusmerge-*"))
    left = subprocess.run(["pgrep", "-f", str(tmp_path)], capture_output=True, text=True).stdout.strip()
    assert left == "", f"engine still running: {left}"


def test_flow_reports_an_import_failure_with_the_saved_path(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=3, addPhotoThrows=True)
    assert res.error is None, res.error
    out = lib / "_DSF1399-2_FM3.tif"
    assert out.exists()                                         # the merge is not lost
    assert any(str(out) in t and "catalog busy" in t for t in _infos(res)), _infos(res)
    assert not list(tmp.glob("focusmerge-*"))


def test_flow_tells_the_user_when_stacking_did_not_happen(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=3, notStacked=True)
    assert res.error is None, res.error
    assert len(res.added) == 1                                  # still imported and selected
    assert any("\u2318G" in t for t in _infos(res)), _infos(res)


def test_flow_refuses_a_relative_work_folder(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=3, prefs={"workFolder": "relative/dir"})
    assert len(res.dialogs) == 1 and "absolute" in res.dialogs[1].info
    assert res.exported is None


def test_flow_refuses_an_unwritable_output_folder_before_exporting(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    s, lib, tmp, res = run_flow(tmp_path, n=3, prefs={"outputFolder": str(blocker / "sub")})
    assert len(res.dialogs) == 1 and "output folder" in res.dialogs[1].info.lower()
    assert res.exported is None


def test_flow_blocks_a_second_run_while_one_is_active(tmp_path):
    s, lib, tmp, res = run_flow(tmp_path, n=3, preBusy=True)
    assert len(res.dialogs) == 1 and "already running" in res.dialogs[1].info
    assert res.exported is None
