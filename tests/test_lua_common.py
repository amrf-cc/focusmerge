import subprocess
from pathlib import Path

import pytest
from lupa import lua51 as lupa  # Lightroom embeds Lua 5.1

PLUGIN = Path(__file__).resolve().parents[1] / "FocusMerge.lrdevplugin"


@pytest.fixture()
def common():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    mod = lua.eval(f"dofile('{PLUGIN / 'Common.lua'}')")
    return lua, mod


def test_shell_quote_roundtrips_through_a_real_shell(common):
    _lua, c = common
    for s in ["plain", "with space", "o'brien", "café ☕", "a;b&c|d", "$HOME `x`",
              "/Volumes/External Drive/Focus Stacks/_DSF1399-2.tif"]:
        # A shell is the thing under test here; the strings are fixed constants, not user input.
        out = subprocess.run(["/bin/sh", "-c", "printf %s " + c.shellQuote(s)],
                             capture_output=True, text=True).stdout
        assert out == s


def test_output_name_and_unique_name(common):
    lua, c = common
    assert c.outputName("_DSF1399-2.tif", 11) == "_DSF1399-2_FM11.tif"
    taken = {"a_FM3.tif", "a_FM3-1.tif"}
    exists = lua.eval("function(set) return function(n) return set[n] == true end end")(
        lua.table_from({k: True for k in taken}))
    assert c.uniqueName("a_FM3.tif", exists) == "a_FM3-2.tif"
    assert c.uniqueName("b.tif", exists) == "b.tif"


def test_sort_entries_by_time_then_name(common):
    lua, c = common
    entries = lua.table_from([
        lua.table_from({"time": 5, "name": "_DSF1402-5.tif"}),
        lua.table_from({"time": 5, "name": "_DSF1399-2.tif"}),
        lua.table_from({"time": 3, "name": "z.tif"}),
    ])
    c.sortEntries(entries)
    assert [entries[i]["name"] for i in (1, 2, 3)] == ["z.tif", "_DSF1399-2.tif", "_DSF1402-5.tif"]


def test_long_edge_pixels(common):
    _lua, c = common
    assert c.longEdgePixels(6240, 4160, 50) == 3120
    assert c.longEdgePixels(4160, 6240, 75) == 4680


def test_export_settings_full_and_draft(common):
    lua, c = common
    full = c.buildExportSettings(lua.table_from({"destination": "/tmp/x", "colorSpace": "ProPhotoRGB"}))
    assert full["LR_format"] == "TIFF" and full["LR_export_bitDepth"] == 16
    assert full["LR_tiff_compressionMethod"] == "compressionMethod_None"
    assert full["LR_outputSharpeningOn"] is False and full["LR_size_doConstrain"] is False
    assert full["LR_export_destinationPathPrefix"] == "/tmp/x"
    draft = c.buildExportSettings(lua.table_from({"destination": "/tmp/x", "longEdgePixels": 3120}))
    assert draft["LR_size_doConstrain"] is True and draft["LR_size_resizeType"] == "longEdge"
    assert draft["LR_size_maxWidth"] == 3120 and draft["LR_size_maxHeight"] == 3120


def test_build_command_quotes_every_path(common):
    lua, c = common
    cmd = c.buildCommand(lua.table_from({
        "python": "/Users/a b/venv/bin/python", "enginePath": "/Lr/Mod ules/FocusMerge.lrplugin/engine",
        "framesFile": "/tmp/focusmerge-1/queue.txt", "out": "/Volumes/External Drive/Focus Stacks/o'k.tif",
        "progress": "/tmp/focusmerge-1/progress.txt", "result": "/tmp/focusmerge-1/result.json",
        "cancel": "/tmp/focusmerge-1/cancel", "error": "/tmp/focusmerge-1/error.txt", "expected": 11, "align": "auto", "sharpness": 2,
        "log": "/Users/a b/Library/Logs/FocusMerge/last-run.log"}))
    assert cmd.startswith("cd '/Lr/Mod ules/FocusMerge.lrplugin/engine' && '/Users/a b/venv/bin/python' -m fm merge")
    assert "--frames-file '/tmp/focusmerge-1/queue.txt' --follow --expected 11" in cmd
    assert "'/Volumes/External Drive/Focus Stacks/o'\\''k.tif'" in cmd
    assert "--delete-inputs" in cmd and cmd.endswith("2>&1")
    assert "--error-file '/tmp/focusmerge-1/error.txt'" in cmd and "--idle-timeout 600" in cmd


def test_parse_progress_and_result(common):
    _lua, c = common
    p = c.parseProgress("3|11|fusing|frame 3")
    assert (p["done"], p["total"], p["stage"], p["message"]) == (3, 11, "fusing", "frame 3")
    assert c.parseProgress("0|?|queued|")["total"] is None
    assert c.parseProgress("garbage") is None
    r = c.parseResult('{"output": "/x/y.tif", "frames": 11, "warnings": ["frame 4: alignment failed", "b"], "seconds": 12.5}')
    assert r["frames"] == 11 and r["output"] == "/x/y.tif"
    assert [r["warnings"][i] for i in (1, 2)] == ["frame 4: alignment failed", "b"]


def test_with_defaults_fills_missing_keys(common):
    lua, c = common
    out = c.withDefaults(lua.table_from({"sizePercent": 50}))
    assert out["sizePercent"] == 50 and out["colorSpace"] == "ProPhotoRGB" and out["sharpness"] == 2
    assert out["workFolder"] == "" and out["align"] == "auto"


def test_all_lua_files_compile():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    compile_ok = lua.eval("function(p) local f, e = loadfile(p); return f ~= nil, e end")
    files = sorted(PLUGIN.glob("*.lua"))
    assert files, "no plugin files found"
    for f in files:
        ok, err = compile_ok(str(f))
        assert ok, f"{f.name}: {err}"


def test_normalize_exit_handles_raw_wait_status(common):
    _lua, c = common
    assert c.normalizeExit(0) == 0 and c.normalizeExit(4) == 4
    assert c.normalizeExit(1024) == 4        # system() style wait status: exit code << 8
    assert c.normalizeExit(None) is None


def test_is_absolute_and_first_line(common):
    _lua, c = common
    assert c.isAbsolute("/Volumes/x") is True
    assert c.isAbsolute("relative/dir") is False and c.isAbsolute("~/x") is False and c.isAbsolute("") is False
    assert c.firstLine("\n  frame 3: dimensions differ  \nsecond") == "frame 3: dimensions differ"
    assert c.firstLine("") is None and c.firstLine(None) is None


def test_shell_quote_refuses_nil(common):
    # A nil path used to become the literal file name 'nil' (a stray engine/nil file shipped once).
    _lua, c = common
    with pytest.raises(Exception, match="nil"):
        c.shellQuote(None)


def test_build_command_refuses_a_missing_path(common):
    lua, c = common
    with pytest.raises(Exception, match="nil"):
        c.buildCommand(lua.table_from({
            "python": "/p/python", "enginePath": "/e", "framesFile": "/q", "out": "/o",
            "progress": "/pr", "result": "/r", "cancel": "/c", "expected": 3}))   # no `error`
