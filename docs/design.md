# FocusMerge design notes

Status: implemented (1.x). Sections 3.2, 4.2, 4.4, 4.5 and 6 were amended after measurement and review; the numbers behind those changes are in `bench/results.md`.

## 1. Purpose

Merge a focus-bracketed stack of photos into one all-in-focus image **without leaving Lightroom
Classic**, much faster than a Photoshop Auto-Align + Auto-Blend round trip.

### Design inputs
- Typical use: 26 MP RAW frames (Fujifilm X-Trans), stacks of 5-45 frames, often shot handheld, in
  Lightroom Classic; the merged result is then finished in Photoshop or back in Lightroom.
- The usual alternative is exporting TIFFs and merging them in another application, which is slow and
  breaks the workflow.
- Target machine class: Apple-silicon Mac, 8 cores, 16 GB RAM, with a nearly full internal disk and
  an external drive for photos, so intermediates must not pile up.
- Python available on the machine cannot be assumed (and a too-new default Python lacks wheels for
  the image libraries), so the installer must bring its own.

### Success criteria
1. One menu click on the selected frames produces a merged, 16-bit TIFF,
   imported into the catalog, stacked with the originals and selected.
2. Wall-clock for a typical 20-frame 26 MP stack is dominated by Lightroom's own
   export; the engine adds only a short tail after the last frame renders.
3. Engine throughput target on an M1 Pro: **≤ ~1 s per 26 MP frame** (to be
   measured, reported honestly; not promised).
4. Image quality on a synthetic ground-truth stack is clearly better than the
   best single frame and no worse than the simple baselines (see §9).
5. Disk use stays flat (no multi-GB pile of intermediates).

### Non-goals (v1)
- Processing RAW directly (Lightroom's render stays the source of colour/lens
  corrections/X-Trans demosaic).
- GPU/Metal engine, DNG output, retouch/brush tools, depth-map output.
- Batch-detecting many stacks in one click (one selection = one stack).
- Windows/Linux support (code stays portable; macOS is the only target tested).

## 2. Architecture

```
Lightroom Classic ──(Lua plugin, runs in LR)──────────────────────────────────┐
  select N photos                                                             │
  LrExportSession ─ renders frame k ─► temp/frame_k.tif ──┐ append path        │
  progress UI ◄─ progress.txt ──────────────────────────  │ to queue file      │
  catalog:addPhoto(result, stackWith=first, 'above')      ▼                    │
                                                  Python engine process        │
                                       follow queue → read → align → fuse →    │
                                       delete frame → … → write result.tif ────┘
```

Two units with a narrow file-based interface:

| Unit | Language | Responsibility | Depends on |
|---|---|---|---|
| `FocusMerge.lrdevplugin` | Lua (Lightroom SDK 6+, Lua 5.1) | selection, export, UX, import, settings | LR SDK, the engine CLI |
| `engine/fm` | Python 3.11–3.13, numpy, OpenCV, tifffile | align + fuse + write | nothing LR-specific |

The engine is usable standalone from a shell on any list of TIFFs, which is also
how it is tested.

## 3. Lightroom plugin

### 3.1 Files
```
FocusMerge.lrdevplugin/
  Info.lua            manifest, menu items (Library + File ▸ Plug-in Extras)
  Common.lua          pure helpers (sorting, naming, command line, progress parse) — unit-testable
  FocusMerge.lua      main flow (async task)
  Settings.lua        settings dialog (LrView/LrBinding), stored in LrPrefs
  PluginInfoProvider.lua  status line in Plug-in Manager (engine found?)
  engine/             the Python engine (see §4)
```

### 3.2 Flow (FocusMerge.lua)
1. `catalog:getTargetPhotos()`; need ≥ 2 photos, none are videos.
2. Sort by `dateTimeOriginal`, tie-break by file name (Fuji names are sequential).
3. Resolve engine: venv python + `engine/` path; if missing, dialog explaining
   `install.sh` and stop.
4. Create work dir `<workFolder or system temp>/focusmerge-<timestamp>/`.
5. Start the engine in its own async task (`LrTasks.execute` blocks that task):
   `python -m fm merge --frames-file queue.txt --follow --expected N --out … --progress-file … --result-file … --cancel-file … --delete-inputs`.
6. In the main task, `LrExportSession` with the settings in §3.3 (progress scope passed with
   `stopIfCanceled`); for each rendition in order: stop if the engine has already exited, then
   `waitForRender()`, then append its path to `queue.txt` (one line, closed immediately; a
   failed append aborts). After the last, append `#END`.
7. A poller task reads `progress.txt` every 250 ms → `LrProgressScope` caption/fraction.
   Cancel in the scope → write `cancel` file + append `#ABORT`.
8. When the engine exits 0: `withWriteAccessDo` (30 s timeout, inside a protected call):
   `catalog:addPhoto(out, firstPhoto, 'above')` — the stack request is made only when the output
   folder is the first photo's folder — then `catalog:setSelectedPhotos(new, {})`. If stacking
   did not take effect, tell the user to press ⌘G; if the import throws, say where the merged
   file was saved and why it could not be added.
9. Clean up work dir (engine deletes frames; plugin removes the dir best-effort).
10. Any failure → `LrDialogs.message` with the reason (the engine writes it to `error.txt`;
    the frame's file name for render failures) and the log path
    (`~/Library/Logs/FocusMerge/last-run.log`). Everything after the engine starts runs in a
    protected call: on any error the engine is told to abort, waited for (bounded), and the
    work folder is removed. Folders are validated (absolute, writable) before anything starts,
    and a second run is refused while one is active.

Result file name: `<firstStem>_FM<count>.tif` in the first original's folder
(default) or the configured output folder; collisions get `-1`, `-2`, ….
After import the new photo is selected, so **⌘E** sends it to Photoshop.

### 3.3 Export settings passed to `LrExportSession`
Key names verified against Lightroom's own `.lrtemplate` export presets:

| Key | Value |
|---|---|
| `LR_format` | `"TIFF"` |
| `LR_export_bitDepth` | `16` |
| `LR_export_colorSpace` | `"ProPhotoRGB"` (setting: sRGB / AdobeRGB / ProPhotoRGB) |
| `LR_tiff_compressionMethod` | `"compressionMethod_None"` (so the engine can clone the container; ZIP also works via fallback) |
| `LR_outputSharpeningOn` | `false` |
| `LR_embeddedMetadataOption` | `"all"` |
| `LR_export_destinationType` | `"specificFolder"` |
| `LR_export_destinationPathPrefix` | work dir |
| `LR_export_useSubfolder` / `LR_export_useParentFolder` | `false` |
| `LR_collisionHandling` | `"rename"` |
| `LR_renamingTokensOn` | `false` |
| `LR_extensionCase` | `"lowercase"` |
| `LR_reimportExportedPhoto` | `false` |
| `LR_size_doConstrain` | `false` for 100 %; for draft sizes `true`, `LR_size_units="pixels"`, `LR_size_resizeType="longEdge"`, `LR_size_maxWidth = LR_size_maxHeight = round(scale × longest edge of first photo's cropped dimensions)` |
| `LR_useWatermark` | `false` |
| `LR_export_postProcessing` | `"doNothing"` |

Develop settings and lens corrections are baked in, exactly like a manual "export TIFFs" step.

### 3.4 Settings (LrPrefs, `com.riveraferran.focusmerge`)
`sizePercent` 100 | 75 | 50 · `colorSpace` · `sharpness` 1–3 (default 2) ·
`align` auto | none · `workFolder` ('' = system temp; user can point at the
external SSD) · `outputFolder` ('' = next to first original) · `pythonPath`
('' = default venv).

## 4. Engine (`engine/fm`)

### 4.1 Modules
| Module | Purpose |
|---|---|
| `cli.py` / `__main__.py` | argument parsing, exit codes |
| `frames_queue.py` | static list or follow-mode queue-file reader (`#END`, `#ABORT`, idle timeout, cancel file) |
| `tiffio.py` | read 16-bit RGB TIFF → uint16 array; write result by cloning a template TIFF |
| `align.py` | frame-to-frame affine estimation + composition, warp, validity mask |
| `fuse.py` | streaming Laplacian-pyramid fusion accumulator |
| `pipeline.py` | two-stage producer/consumer (read+align+warp ‖ fuse), progress, cleanup |

### 4.2 CLI contract
```
python -m fm merge (--frames-file Q [--follow --expected N] | FRAME…)
                   --out OUT.tif
                   [--align auto|none] [--sharpness 1|2|3]
                   [--delete-inputs] [--progress-file P] [--result-file R]
                   [--cancel-file C] [--error-file E] [--idle-timeout SECONDS=900]
```
- **Queue file**: UTF-8 lines, one absolute path each; `#END` finishes; `#ABORT`
  aborts. Only newline-terminated lines are consumed.
- **Progress file** (atomically replaced): `done|total|stage|message`.
- **Result file**: JSON `{output, frames, aligned, warnings[], seconds,
  seconds_per_frame, metadata: "container"|"exiftool"|"none", peak_rss_mb}`.
- **Exit codes**: 0 ok · 2 usage · 3 fewer than 2 frames · 4 input/IO/format
  error · 5 cancelled · 6 internal error (including a non-finite fusion result). A one-line
  reason goes to stderr and to `--error-file`. The cancel file is re-checked immediately before
  the result is written.
- `--delete-inputs` removes each frame after it is consumed, refusing any path
  whose parent directory name does not start with `focusmerge-`.

### 4.3 Reading and writing TIFFs
- Read with `tifffile` → `(H, W, 3)` uint16 (alpha dropped; uint8 promoted).
  Compression (None/ZIP/LZW) handled by tifffile + imagecodecs.
- **Write by cloning**: copy the first frame to `OUT`, then overwrite its strip
  data in place (requirements: uncompressed, chunky RGB, 16-bit, striped,
  identical dimensions). This preserves EXIF, XMP, ICC and byte order exactly,
  with zero extra dependencies.
- **Fallback** when cloning is impossible (compressed/tiled/size changed):
  `tifffile.imwrite` with the ICC profile copied, then `exiftool
  -TagsFromFile` if `exiftool` is on PATH; `metadata` in result.json records
  which path was taken.

### 4.4 Alignment
- Reference geometry = frame 0 (streaming-friendly, frame 0 always covers every
  output pixel).
- Per frame: grayscale → area-downscale so the long edge is ≤ 1600 px → light blur, then
  `cv2.findTransformECC` (MOTION_AFFINE, three levels ¼ → ½ → 1× of that size, no separate
  phase-correlation seed: the coarse level absorbs handheld shifts) against the **previous
  registered** frame. Chain transforms `M_i = W_i · M_{i-1}` (the approach stacking tools use
  because far-apart frames differ in blur).
- Affine covers translation, rotation and focus-breathing scale.
- A registration is believed when ECC converged, the transform is plausible (scale ±5 %,
  rotation ±5°, shift ±10 % of the frame) **and** the pictures are similar enough *for how far
  the frame moved*: correlation ≥ 0.95 normally, ≥ 0.80 when the motion is small (≤ 1 % of the
  long edge, ≤ 1 % scale, ≤ 1°). Neighbouring frames of a real stack correlate at ≥ 0.999, a
  camera knock scored 0.927 on a real 22-frame stack (a single 0.95 gate rejected it and then
  everything after it), re-framed or blank shots score ≤ 0.9 with larger motion.
- Registration is anchored, never sticky. A hop is *sure* when its correlation is ≥ 0.95 against
  a sure reference. The frame after anything that is not sure (a failed hop, or one believed on
  moderate similarity) is registered against the last sure frame first — an independent, clean
  estimate that recovers drift a failed hop lost — and against the previous frame if it is too
  far from the anchor in focus. A frame that cannot be registered at all is merged with its
  neighbour's geometry and a warning; it never makes later frames fail.
- **Registers to the scene, not to what is in front of it.** Two things defeat a plain ECC fit
  and both occur with hand-held lights: an object that moves independently (a hand, a light:
  large, high-contrast, so it outweighs the fine texture of the scene by ~250x and bends the fit by
  several px everywhere else) and lighting that differs from frame to frame (plain ECC then
  returns nonsense, 100-300 px off). The estimate therefore (a) takes a plain fit, and keeps it
  when it correlates at >= 0.97, shows no region that disagrees with it and a contrast-normalised
  refinement started from it moves it by < 0.25 px (every ordinary hop on real stacks: the
  unchanged fast path); (b) otherwise also fits on locally contrast-normalised images and chooses
  by a trimmed residual measured in log-intensity high-pass space (invariant to smooth lighting):
  the normalised fit when a moving region exists and it explains the scene at least as well, or
  when it is clearly better (changed light); the plain fit otherwise (a frame smeared by motion
  blur, where normalising hurts); (c) refits the chosen fit without the regions that disagree and
  reports the correlation over the pixels that were used. **Fusion is not changed**: a moving
  object is blended like anything else and stays ghosted, which is what a user who layers
  hand-held-light exposures over a base image wants (an attempt to use the first frame alone in
  those regions was withdrawn: it would also have thrown away the lighting variation).
- Warp at full resolution with `cv2.warpAffine` (bicubic — measurably indistinguishable from
  Lanczos on these stacks and ~4× cheaper — border replicate) on uint16; build a validity mask
  from the transformed frame rectangle (eroded 2 px) so out-of-frame areas contribute zero
  weight. Shifts under 0.05 px skip the warp entirely.
- `--align none` skips estimation (tripod + rail, or when the user knows frames are registered).

### 4.5 Fusion (streaming, constant memory in frame count)
For each aligned frame, float32 RGB in [0,1]:
1. **Focus map**: luminance → Gaussian σ 0.7 → |Laplacian| → Gaussian σ 2 → scaled by
   1/0.02, floor 0.05 → raised to `p` (`p = 8 / 16 / 24` for sharpness 1/2/3, default 16, by
   repeated squaring) → multiplied by the validity mask. One map per frame, full resolution.
2. Gaussian pyramids of the frame (`cv2.pyrDown`, `N = min(7, floor(log2(min(H,W))) − 4)`
   levels) and of the focus map.
3. Laplacian bands `L_l = G_l − pyrUp(G_{l+1})`.
4. Accumulate `A_l += W_l · L_l`, `S_l += W_l` for every band and for the low-pass residual
   (`W_l` = the focus map's Gaussian pyramid at level `l`).

Finish: `out_l = A_l / S_l`, collapse the pyramid, clip, convert to uint16.

Why this design: one focus decision per region shared by all bands (no per-band
disagreement), smooth seams at coarse scales (the weight pyramid is low-passed with the
band), averaging in flat areas (noise drops), and one focus map instead of one per band
(cheaper). Measured on the synthetic ground-truth stack (single-frame oracle ceiling
54 dB): per-band energy weights 46–49 dB, this design 50.0–50.6 dB at low noise and the
best variant tested at higher noise. The base is normalised and then clamped so that
`base^p` cannot overflow float32 (cap ≈ 139 at `p = 16`, never reached on real frames;
≈ 27 at `p = 24`, where only the hardest edges saturate to equal weights). The first version
claimed normalisation alone was enough; review showed a full-contrast edge at `p = 24` turned
into NaN and black pixels, hence the clamp and the finite-result guard in the pipeline. The exponent must grow with the frame count: every out-of-focus frame keeps a small
weight and 10–45 of them add up. On the real 11-frame X-S20 stack `p = 8` kept the sharpest
regions at a median 0.83 of the best frame's detail, `p = 16` at 0.98, so 16 is the default.

### 4.6 Performance design
- Stage A (read + convert + align + warp) and stage B (fuse) run as a
  two-thread pipeline with a queue of depth 1–2; OpenCV releases the GIL.
- Pipeline overlaps with Lightroom's rendering via follow mode.
- Memory: accumulators ≈ 1.33 × (3+1) float32 planes ≈ 0.55 GB at 26 MP plus
  current pyramid and queue ≈ 2 GB peak; frames are deleted as consumed.
- Expected ≈ 0.6–1.0 s/frame at 26 MP on M1 Pro. **Measured numbers will go in
  the README, whatever they are.** If far off target, the profiler decides
  between tiling/threads and the Metal path (out of scope for v1).

## 5. Install
`install.sh` (idempotent, user-level, no sudo; refuses to run as root):
1. Preflight: macOS 13+ (Apple silicon) or 14+ (Intel), the floor of the OpenCV wheels.
2. Python 3.11-3.13 from the PATH, else its own: a **pinned uv release** downloaded from
   github.com/astral-sh/uv with its SHA-256 verified before anything is unpacked or run (no remote
   script is ever piped into a shell), which then fetches Python 3.12 into
   `~/Library/Application Support/FocusMerge/python`.
3. Create `~/Library/Application Support/FocusMerge/venv` (reused when healthy) and
   `pip install -r engine/requirements.txt`.
4. Copy the plugin to `~/Library/Application Support/Adobe/Lightroom/Modules/FocusMerge.lrplugin`.
5. Self-test: merge a tiny generated stack.

`--uninstall` removes those two folders. Double-clickable `Install/Uninstall FocusMerge.command`
wrap it for users who do not use Terminal.

## 6. Error handling summary
| Situation | Behaviour |
|---|---|
| < 2 photos / video selected | dialog, nothing started |
| Engine/venv missing | dialog with the install command |
| A rendition fails | stop export, `#ABORT`, dialog listing the frame and LR's message |
| Engine exit ≠ 0 | dialog with reason + log path, temp dir removed |
| Alignment fails on a frame | frame still used unaligned, warning shown in the final dialog |
| Cancel | `#ABORT` + cancel file; no result imported; temp removed |
| Disk full / write error | engine exit 4, reason shown |
| Out-of-order or tied timestamps | name tiebreak; engine tolerates imperfect order |

## 7. Settings, files and locations
| Item | Location |
|---|---|
| Plugin (installed) | `~/Library/Application Support/Adobe/Lightroom/Modules/FocusMerge.lrplugin` |
| Python venv | `~/Library/Application Support/FocusMerge/venv` |
| Last-run log | `~/Library/Logs/FocusMerge/last-run.log` |
| Temp frames | `<workFolder|system temp>/focusmerge-<timestamp>/` (auto-deleted) |
| Merged result | next to the first original (or the output folder setting) |

## 8. Risks and open items
1. **`addPhoto(..., 'above')` stacking** has a community report of not stacking
   on some versions; the import still succeeds. Mitigation: check
   `newPhoto:getRawMetadata('isInStackInFolder')` after import; if false, tell
   the user to stack manually (⌘G); do not fail the run.
2. **Rendition order** is not specified in SDK docs; we rely on session order
   and on the engine tolerating mild disorder.
3. **Export keys** were verified against Lightroom export-preset files; the
   `LR_size_*` keys were verified from stock presets; `compressionMethod_None`
   is confirmed by community reports and has a working fallback.
4. **Lightroom cannot be driven from this build environment** without touching
   a real catalog, so the in-LR path is verified by static review, a
   mocked-SDK test of the Lua logic, and a user smoke test (§9).
5. **Quality on pathological scenes** (large smooth regions, specular
   highlights, moving subjects) is inherent to pixel fusion; `sharpness`
   gives a knob. Not a retouching tool.

## 9. Testing
**Engine (pytest, synthetic ground truth)**
- Scene generator: textured planes at several depths, per-frame depth-dependent
  Gaussian blur, plus per-frame affine (scale 1+0.002·i, shifts, tiny rotation),
  noise, written as 16-bit strip TIFFs with an ICC profile and EXIF-like tags.
- Assertions: alignment recovers the known transform within a fraction of a
  pixel; fused PSNR vs. truth ≥ best single frame + 3 dB and ≥ the plain
  weighted-average baseline (thresholds are calibrated on the first measured run and then frozen); output pixel dimensions and
  all non-image TIFF tags (ICC, Make/Model/Software, EXIF IFD) equal the first
  frame's; fallback writer path; follow mode with a delayed writer thread;
  `#ABORT`/cancel/idle-timeout/exit codes; `--delete-inputs` guard.
- Benchmark script at 6240×4160: s/frame, peak RSS.

**Lua**
- `Common.lua` pure functions tested under Lua 5.1 (via `lupa`) with a mocked
  `import`.
- The main flow executed against a mocked LR SDK to catch typos, wrong call
  order and unhandled nil paths.

**Manual smoke test in Lightroom (5 minutes)**
Select 3 frames of an existing stack → *Library ▸ Plug-in Extras ▸ Focus Merge
Selected Photos* → expect progress, a new `*_FM3.tif` stacked above the first
frame and selected; temp folder empty afterwards.

