import json
import subprocess
import sys
from pathlib import Path

import pytest

from fm.cli import main
from synth import make_stack

ENGINE_DIR = Path(__file__).resolve().parents[1] / "FocusMerge.lrdevplugin" / "engine"


def test_merge_ok_writes_result_file(tmp_path):
    s = make_stack(tmp_path / "in", n=3, h=96, w=128)
    out, res = tmp_path / "o.tif", tmp_path / "r.json"
    code = main(["merge", *map(str, s.paths), "--out", str(out), "--result-file", str(res)])
    assert code == 0 and out.exists()
    assert json.loads(res.read_text())["frames"] == 3


def test_exit_code_3_for_one_frame(tmp_path, capsys):
    s = make_stack(tmp_path / "in", n=2, h=96, w=128)
    assert main(["merge", str(s.paths[0]), "--out", str(tmp_path / "o.tif")]) == 3
    assert "at least 2" in capsys.readouterr().err


def test_exit_code_4_for_missing_file(tmp_path, capsys):
    assert main(["merge", "/nonexistent/a.tif", "/nonexistent/b.tif", "--out", str(tmp_path / "o.tif")]) == 4
    assert "cannot read" in capsys.readouterr().err


def test_exit_code_5_on_abort(tmp_path):
    s = make_stack(tmp_path / "in", n=2, h=96, w=128)
    q = tmp_path / "q.txt"
    q.write_text(f"{s.paths[0]}\n#ABORT\n")
    assert main(["merge", "--frames-file", str(q), "--follow", "--out", str(tmp_path / "o.tif")]) == 5


def test_usage_errors_exit_2(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["merge", "--out", str(tmp_path / "o.tif")])          # no frames at all
    assert e.value.code == 2


def test_selftest():
    assert main(["selftest"]) == 0


def test_python_dash_m_entry_point(tmp_path):
    s = make_stack(tmp_path / "in dir", n=3, h=96, w=128)
    out = tmp_path / "o p.tif"
    r = subprocess.run([sys.executable, "-m", "fm", "merge", *map(str, s.paths), "--out", str(out)],
                       cwd=ENGINE_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.exists()


def test_error_file_receives_the_reason(tmp_path):
    err = tmp_path / "error.txt"
    code = main(["merge", "/nonexistent/a.tif", "/nonexistent/b.tif", "--out", str(tmp_path / "o.tif"),
                 "--error-file", str(err)])
    assert code == 4
    assert "cannot read" in err.read_text()


def test_error_file_for_not_enough_frames(tmp_path):
    s = make_stack(tmp_path / "in", n=2, h=96, w=128)
    err = tmp_path / "error.txt"
    assert main(["merge", str(s.paths[0]), "--out", str(tmp_path / "o.tif"), "--error-file", str(err)]) == 3
    assert "at least 2" in err.read_text()
