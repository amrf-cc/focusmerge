# FocusMerge

[![CI](https://github.com/amrf-cc/focusmerge/actions/workflows/ci.yml/badge.svg)](https://github.com/amrf-cc/focusmerge/actions/workflows/ci.yml)

One-click focus stacking from inside Lightroom Classic. Select the frames of a stack, run
**Library ▸ Plug-in Extras ▸ Focus Merge Selected Photos**, and a merged 16-bit TIFF appears in your
catalog, stacked above the first frame and selected. No round trip through Photoshop or another
stacking app for the merge itself.

<table>
<tr>
<td width="50%"><img src="docs/images/bf.jpg" alt="One frame of the stack"></td>
<td width="50%"><img src="docs/images/af.jpg" alt="The finished photo"></td>
</tr>
<tr>
<td align="center"><sub>One frame (shallow depth of field)</sub></td>
<td align="center"><sub>The finished photo: a focus stack merged with FocusMerge, then edited</sub></td>
</tr>
</table>

It aligns the frames (handheld shift, focus breathing, a hand-held light in frame), fuses them with
a sharpness-weighted Laplacian pyramid, and keeps your EXIF and colour profile.

- Works on what Lightroom renders, so your develop settings and lens corrections are baked in.
- Pipelined: each frame is aligned and merged while Lightroom renders the next one.
- Constant memory (about 3–4 GB for 26 MP frames, whatever the number of frames) and no pile of
  intermediates: frames are deleted as they are consumed.

## Requirements

- macOS 13 or newer on Apple silicon, or macOS 14 or newer on Intel
- Lightroom Classic
- An internet connection during installation (roughly 150–200 MB, once)
- About 1 GB of free disk space for the engine; 16 GB of RAM is comfortable for 26 MP stacks

## Install

Download the release zip, unzip it and **double-click `Install FocusMerge.command`**. The first time,
macOS may say it cannot verify the developer: right-click the file, choose **Open**, then **Open**.
Then restart Lightroom Classic.

Or from a terminal:

```bash
bash install.sh
```

The installer needs nothing beforehand. If the Mac has no Python 3.11–3.13 it downloads its own
(through [uv](https://docs.astral.sh/uv/)) into FocusMerge's own folder. It writes only to:

- `~/Library/Application Support/FocusMerge` (engine, private Python environment)
- `~/Library/Application Support/Adobe/Lightroom/Modules/FocusMerge.lrplugin` (the plugin)
- `~/Library/Logs/FocusMerge` (last run's log)

No `sudo`, no shell profile changes. Nothing is sent anywhere. To uninstall, double-click
`Uninstall FocusMerge.command` or run `bash install.sh --uninstall`.

## Use

1. Select the frames of one focus stack in the Library grid (any order; they are sorted by capture
   time, then file name).
2. **Library ▸ Plug-in Extras ▸ Focus Merge Selected Photos** (or **File ▸ Plug-in Extras**).
3. Watch the progress bar. Frames are exported by Lightroom and merged while the next one renders.
4. The result `<first file>_FM<count>.tif` is added next to the first original and stacked above it.
   It is selected, so **⌘E** sends it straight to Photoshop.

A keyboard shortcut for the menu item: System Settings ▸ Keyboard ▸ Keyboard Shortcuts ▸ App
Shortcuts ▸ Lightroom Classic ▸ menu title `Focus Merge Selected Photos`.

### Settings (Library ▸ Plug-in Extras ▸ Focus Merge Settings…)

| Setting | Default | Notes |
|---|---|---|
| Output size | 100 % | 75 % / 50 % export smaller frames: a fast draft |
| Colour space | ProPhoto RGB | Adobe RGB / sRGB available |
| Sharpness bias | Balanced (2) | 1 = smoother, for very noisy frames; 3 = crisper |
| Alignment | Automatic | None for tripod/rail stacks that are already registered |
| Work folder | system temp | Point at a fast external drive if the main disk is nearly full |
| Output folder | next to first photo | |
| Python (advanced) | the installed environment | |

## Hand-held lights and hands in frame

A hand-held light, a hand or a reflector in the frame does not pull the alignment: the stack is
registered to the *static scene* (so exposure layers from separate light shots line up with your
original), and lighting that changes between frames does not confuse it. The moving object itself is
blended like everything else, so it stays ghosted; mask or clean it up afterwards.

## Speed

Measured on an Apple M1 Pro (8 cores, 16 GB) with 11 handheld 26 MP frames, 16-bit ProPhoto
(`bench/results.md` in the source repository):

| | Engine time | Per frame |
|---|---|---|
| 11 frames, uncompressed TIFFs (what the plugin feeds the engine) | 5.5 – 9 s | 0.5 – 0.8 s |
| 11 frames, Lightroom ZIP TIFFs | 6.8 s | 0.6 s |

Those are quiet-machine figures. While Lightroom is busy at the same time (its own rendering uses all
cores) the same merge took 12 – 23 s, i.e. 1.1 – 2 s per frame. The engine works while Lightroom
renders the next frame, so what you wait for is mostly Lightroom's own export.

## How it works

```
Lightroom (Lua plugin)                      Python engine (python -m fm merge)
  export frame k as 16-bit TIFF ──queue──►  read → register to previous frame (ECC affine, chained)
  progress bar ◄──────progress file───────  → warp → fuse into a streaming Laplacian pyramid
  import result, stack, select ◄──result──  → delete the frame → … → write result (clone of frame 1's
                                              TIFF container, so EXIF/ICC survive byte for byte)
```

Fusion weights every pyramid band with the Gaussian pyramid of one focus map per frame, so the
sharpest frame wins each region while seams stay smooth and flat areas average noise down.
Registration is anchored (one failed frame never makes later frames fail) and robust to moving
objects and changing light. Design notes are in `docs/design.md` and measurements in `bench/results.md`.

## Troubleshooting

- **"Focus Merge is not installed"**: run the installer again, or set *Python* in the settings.
- **A merge fails**: the dialog states the reason from the engine (for example a frame of a different
  size, or a folder that cannot be written). The log of the last run is
  `~/Library/Logs/FocusMerge/last-run.log`; attach it when asking for help. The export stops as soon
  as the engine has failed.
- **Cancel**: the progress bar's cancel stops the export and the merge; no result is imported and the
  work folder is removed.
- **"frame N: alignment failed" notes after a merge**: that frame could not be matched to its
  neighbours (a blank or re-framed shot, or a very large knock) and was merged with its neighbour's
  geometry; later frames are not affected. If several frames in a row are reported, check them in
  Lightroom; leaving the odd frame out of the selection is the fix.
- **"N s waiting for frames" in the log**: the engine works while Lightroom renders the next frame, so
  most of a long merge is Lightroom's own export time, not the engine.
- **Result not stacked with the originals**: Lightroom's `addPhoto` stacking is not reliable on every
  version; select the original and the result and press ⌘G.
- **Metadata**: with Lightroom's uncompressed export the result's container is cloned. If frames come
  in compressed, metadata is copied with `exiftool` when it is installed (`brew install exiftool`).
- **Disk space**: frames are deleted as they are consumed, so about three frames of scratch space is
  enough. Set *Work folder* to an external drive if the system disk is nearly full.

## Limits

- Pixel-level fusion: subjects that move between frames, steam, and highlights that change with
  focus can produce artefacts. It is not a retouching tool.
- macOS only. Developed and tested on Apple silicon with Lightroom Classic 15 and Fujifilm X-Trans
  files; other cameras should work (the engine reads Lightroom's TIFFs) but are untested.

## Development

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q          # engine, Lua flow (real engine + mocked SDK), installer, release
bash scripts/make_release.sh           # dist/FocusMerge-<version>.zip
```

Layout: `FocusMerge.lrdevplugin/` (Lua plugin + `engine/fm`), `tests/`, `bench/`, `docs/`,
`install.sh`, `scripts/`.

## Contributing and security

Bug reports and pull requests are welcome: see `CONTRIBUTING.md`. For security issues see
`SECURITY.md`. Nothing is uploaded anywhere; there is no telemetry.

## License

MIT, see `LICENSE`. Third-party components installed by the installer are listed in
`THIRD_PARTY_NOTICES.md`. Adobe and Lightroom are trademarks of Adobe Inc.; this project is not
affiliated with Adobe.
