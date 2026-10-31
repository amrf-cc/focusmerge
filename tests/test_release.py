import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "FocusMerge.lrdevplugin"


def engine_version():
    text = (PLUGIN / "engine" / "fm" / "__init__.py").read_text()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def test_versions_agree_everywhere():
    v = engine_version()
    info = (PLUGIN / "Info.lua").read_text()
    m = re.search(r"VERSION = \{ major = (\d+), minor = (\d+), revision = (\d+)", info)
    assert ".".join(m.groups()) == v
    assert re.search(rf"^## {re.escape(v)}\b", (ROOT / "CHANGELOG.md").read_text(), re.M)


@pytest.fixture(scope="module")
def release_zip(tmp_path_factory):
    dist = tmp_path_factory.mktemp("dist")
    r = subprocess.run(["bash", str(ROOT / "scripts" / "make_release.sh")], cwd=ROOT,
                       env={**os.environ, "FOCUSMERGE_DIST": str(dist)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    zips = list(dist.glob("FocusMerge-*.zip"))
    assert len(zips) == 1, zips
    return zips[0]


def test_release_zip_has_what_a_colleague_needs_and_nothing_else(release_zip):
    v = engine_version()
    assert release_zip.name == f"FocusMerge-{v}.zip"
    with zipfile.ZipFile(release_zip) as z:
        names = z.namelist()
    top = f"FocusMerge-{v}/"
    assert all(n.startswith(top) for n in names), "everything sits in one folder"
    rel = {n[len(top):] for n in names}
    for need in ("install.sh", "Install FocusMerge.command", "Uninstall FocusMerge.command",
                 "START HERE.txt", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md",
                 "FocusMerge.lrdevplugin/Info.lua", "FocusMerge.lrdevplugin/MergeFlow.lua",
                 "FocusMerge.lrdevplugin/engine/requirements.txt", "FocusMerge.lrdevplugin/engine/fm/cli.py"):
        assert need in rel, need
    bad = [n for n in rel if re.search(r"(^|/)(tests|bench|docs|scripts|\.git|\.venv|\.remember|__pycache__)(/|$)", n)
           or n.endswith((".pyc", ".DS_Store"))]
    assert not bad, bad


def test_release_zip_keeps_executable_bits(release_zip):
    v = engine_version()
    with zipfile.ZipFile(release_zip) as z:
        for name in ("install.sh", "Install FocusMerge.command", "Uninstall FocusMerge.command"):
            info = z.getinfo(f"FocusMerge-{v}/{name}")
            assert (info.external_attr >> 16) & 0o111, f"{name} lost its executable bit"


def test_the_shipped_zip_installs_into_a_sandbox(release_zip, tmp_path):
    subprocess.run(["unzip", "-q", str(release_zip), "-d", str(tmp_path / "unzipped")], check=True)
    folder = next((tmp_path / "unzipped").iterdir())
    env = {**os.environ, "FOCUSMERGE_SUPPORT": str(tmp_path / "support"),
           "FOCUSMERGE_LR_MODULES": str(tmp_path / "modules")}
    r = subprocess.run(["bash", str(folder / "install.sh")], cwd=folder, env=env,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "modules" / "FocusMerge.lrplugin" / "Info.lua").exists()
    assert "selftest ok" in r.stdout
