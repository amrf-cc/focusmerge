# Contributing to FocusMerge

Thanks for helping. Bug reports with a log are the most useful contribution; code is welcome too.

## Reporting a problem

Open an issue and fill in the template. Please include:

- macOS and Lightroom Classic versions, and your Mac's chip (Apple silicon or Intel);
- camera / file type, number of frames, and the plugin's Output size setting;
- the log of the last run: `~/Library/Logs/FocusMerge/last-run.log`.

Please do not attach photos you do not have the right to share.

## Development setup

You do not need Lightroom to work on the engine or to run the tests: the plugin's flow is tested
against a mocked Lightroom SDK that drives the real engine.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Useful variants:

```bash
FOCUSMERGE_TEST_BOOTSTRAP=1 .venv/bin/python -m pytest tests/test_install.py   # also tests the no-Python install and the pinned uv checksums (downloads ~250 MB)
bash scripts/make_release.sh                                                  # builds dist/FocusMerge-<version>.zip
```

## How the code is organised

- `FocusMerge.lrdevplugin/*.lua` is the Lightroom plugin (Lua 5.1: avoid `goto`, `//`, bit
  operators, `table.unpack`). `Common.lua` holds pure helpers; `MergeFlow.lua` the whole flow, with
  every SDK handle passed in so it can be tested.
- `FocusMerge.lrdevplugin/engine/fm/` is the Python engine (alignment in `align.py`, fusion in
  `fuse.py`, the pipeline in `pipeline.py`).
- `docs/design.md` explains the design and why; `bench/results.md` records the measurements behind
  the non-obvious choices.

## Pull requests

- Keep a change focused and add a test that fails without it. Behaviour changes to alignment or
  fusion should come with the measurement that motivates them (see `bench/`).
- Run the full test suite before opening the PR; CI runs it on macOS.
- Lightroom itself cannot run in CI, so describe how you tried the plugin by hand if you touched
  `MergeFlow.lua`, the export settings, or `Info.lua`.
- By contributing you agree that your contribution is released under the MIT License.
