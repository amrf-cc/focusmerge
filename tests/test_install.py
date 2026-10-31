import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(args, env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.run(["bash", str(ROOT / "install.sh"), *args], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=900)


def test_install_and_uninstall_into_sandbox(tmp_path):
    support, modules = tmp_path / "support", tmp_path / "modules"
    env = {"FOCUSMERGE_SUPPORT": str(support), "FOCUSMERGE_LR_MODULES": str(modules)}
    r = run([], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (modules / "FocusMerge.lrplugin" / "Info.lua").exists()
    assert (modules / "FocusMerge.lrplugin" / "engine" / "fm" / "cli.py").exists()
    assert not list((modules / "FocusMerge.lrplugin").rglob("__pycache__"))
    assert (support / "venv" / "bin" / "python").exists()
    assert "selftest ok" in r.stdout
    r2 = run(["--uninstall"], env)
    assert r2.returncode == 0
    assert not (modules / "FocusMerge.lrplugin").exists() and not support.exists()


def test_old_macos_is_refused_with_a_clear_message(tmp_path):
    env = {"FOCUSMERGE_SUPPORT": str(tmp_path / "s"), "FOCUSMERGE_LR_MODULES": str(tmp_path / "m"),
           "FOCUSMERGE_MACOS_VERSION": "12.6"}
    r = run([], env)
    assert r.returncode != 0
    assert "needs macOS 1" in r.stderr and "12.6" in r.stderr
    assert not (tmp_path / "m").exists()          # nothing was touched


def test_double_click_launchers_are_executable_and_call_the_installer():
    for name, arg in (("Install FocusMerge.command", "install.sh"), ("Uninstall FocusMerge.command", "--uninstall")):
        f = ROOT / name
        assert os.access(f, os.X_OK), name
        text = f.read_text()
        assert text.startswith("#!/bin/bash") and "install.sh" in text and arg in text


@pytest.mark.skipif(not os.environ.get("FOCUSMERGE_TEST_BOOTSTRAP"),
                    reason="downloads uv, Python and the libraries (~250 MB); set FOCUSMERGE_TEST_BOOTSTRAP=1")
def test_installs_on_a_mac_with_no_python_and_no_uv(tmp_path):
    env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "FOCUSMERGE_SUPPORT": str(tmp_path / "support"), "FOCUSMERGE_LR_MODULES": str(tmp_path / "modules")}
    r = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, cwd=ROOT,
                       capture_output=True, text=True, timeout=1500)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest ok" in r.stdout
    assert (tmp_path / "support" / "python").exists() and (tmp_path / "support" / "bin" / "uv").exists()


def _install_text():
    return (ROOT / "install.sh").read_text()


def test_installer_never_pipes_a_remote_script_into_a_shell():
    # Publicly distributed installer: it must not execute whatever a server returns. Downloads are
    # pinned release files whose SHA-256 is checked before anything is unpacked or run.
    text = _install_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "| sh" not in code and "| bash" not in code and "|sh" not in code
    assert "astral.sh/uv/install.sh" not in code
    assert "shasum -a 256" in code
    for arch in ("AARCH64", "X86_64"):
        m = re.search(rf'^UV_SHA256_{arch}="([0-9a-f]{{64}})"$', text, re.M)
        assert m, f"pinned uv checksum for {arch} missing"
    assert re.search(r'^UV_VERSION="\d+\.\d+\.\d+"$', text, re.M)


def test_installer_refuses_to_run_as_root():
    assert re.search(r'\$\{?EUID[:}\-]|id -u', _install_text()), "no root check"
    assert "not with sudo" in _install_text()


@pytest.mark.skipif(not os.environ.get("FOCUSMERGE_TEST_BOOTSTRAP"),
                    reason="downloads two ~15 MB release files; set FOCUSMERGE_TEST_BOOTSTRAP=1")
def test_pinned_uv_checksums_match_the_published_release(tmp_path):
    import hashlib
    import urllib.request
    text = _install_text()
    version = re.search(r'^UV_VERSION="([^"]+)"$', text, re.M).group(1)
    for arch in ("aarch64", "x86_64"):
        want = re.search(rf'^UV_SHA256_{arch.upper()}="([0-9a-f]{{64}})"$', text, re.M).group(1)
        url = f"https://github.com/astral-sh/uv/releases/download/{version}/uv-{arch}-apple-darwin.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as r:
            assert hashlib.sha256(r.read()).hexdigest() == want, arch
