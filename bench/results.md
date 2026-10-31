# Real-data results

Machine: Apple M1 Pro, 8 cores, 16 GB RAM, internal SSD ~96 % full. Python 3.11, OpenCV 5.0.0, numpy 2.4.
Frames: 11 handheld Fujifilm X-S20 exports (XF16-55mm F2.8 at 29 mm, f/7.1, 1/125 s, ISO 1250),
6240 × 4160, 16-bit ProPhoto RGB, Lightroom ZIP + horizontal-differencing TIFFs:
`_DSF1399-2, _DSF1402-5 … _DSF1411-14` from `/Volumes/External Drive/Focus Stacks` (read only).
Final defaults: alignment `auto`, sharpness 2 (weight exponent p = 16).

Commands: `bench/real_stack.py` (see the script header), `bench/prepare_uncompressed.py` for the
uncompressed copies that stand in for what the plugin's export (`compressionMethod_None`) produces.

## Speed and memory (engine only, wall clock including reading and writing the files)

| Input | Frames | Wall | Per frame | Peak RSS | Output metadata path |
|---|---|---|---|---|---|
| Original ZIP TIFFs (external SSD) | 11 | 6.8 s | 0.62 s | 3.2 GB | exiftool copy |
| Uncompressed copies (plugin's format) | 11 | 5.4 – 5.5 s | 0.50 s | 3.3 – 3.4 GB | container clone |
| Uncompressed, 11 frames × 4 (44 frames) | 44 | 16.9 s | 0.38 s | 3.8 GB | container clone |

- Earlier runs of the same stack at a heavier moment on the machine took 9.0 s (0.82 s/frame);
  expect 0.5 – 0.8 s/frame at 26 MP. The spec target was ≤ ~1 s/frame.
- Memory is flat in the number of frames: 3.4 GB for 11 frames, 3.8 GB for 44.
- The 44-frame run repeats the 11 frames, so alignment converges immediately there; its per-frame
  time understates a real 44-frame stack. The memory figure is the point of that run.
- In the plugin the engine runs while Lightroom renders the next frame, so only the last frame's
  processing plus the final collapse and write (~1 s) is added after Lightroom's export.

## Alignment on the real handheld frames

Smooth, monotonic drift across the stack, no alignment warnings, no frame needed the fallback:

```
_DSF1399-2    shift=(  0.0,  0.0)px scale=1.0000
_DSF1402-5    shift=( -1.9,  0.2)px scale=1.0005
_DSF1404-7    shift=( -3.1,  0.4)px scale=1.0008
_DSF1406-9    shift=( -3.6, -1.0)px scale=1.0012
_DSF1408-11   shift=( -4.3, -1.8)px scale=1.0015
_DSF1411-14   shift=( -5.5, -2.7)px scale=1.0019   (focus breathing ~0.19 %, rotation < 0.02°)
```

## Quality

Visual check of 600 × 900 px crops at 100 % (centre, upper, lower): the merged result resolves cheese,
crumb and crust detail that frame 0 loses completely; edges of the slice against the paper are clean
(no halo, no double edges); flat paper is free of speckle; colour and tone match the frames.

Metric `fused / sharpest-aligned-frame` per 64 px tile (Laplacian variance of luminance after a
σ 1.2 pre-blur so sensor noise does not inflate it):

| Setting | p5 | p25 | median | p75 | p95 |
|---|---|---|---|---|---|
| p = 8 (first design) | 0.40 | 0.51 | 0.83 | 0.93 | 0.98 |
| **p = 16 (default)** | 0.73 | 0.86 | **0.98** | 1.02 | 1.07 |

## Findings that changed the code

1. **Per-band energy weighting** (the plan's first design) scored 45.9 dB vs 50.1 dB for a plain
   pixel-domain baseline on the synthetic ground-truth stack. Replaced by one focus map per frame whose
   Gaussian pyramid weights every band (50.0 – 50.6 dB; oracle ceiling 54 dB).
2. **Exponent p = 8 softened the sharpest regions of real stacks** (median 0.83, worst tiles 0.31):
   ten defocused frames at a few percent each add up. p = 16 fixes it (median 0.98, worst tiles
   0.57 – 0.70). Regression test: `test_many_frames_do_not_soften_the_sharp_one`.
   Cost: p = 16 loses ~0.5 dB on synthetic stacks with very high noise; sharpness 1 (p = 8) is the
   setting for very noisy frames.

## After the final review (fresh-context reviewer, Opus)

- **Sharpness 3 (p = 24) overflowed float32 on full-contrast edges** and wrote NaN as black pixels
  (reproduced: 100 % of a stripes image; the reviewer saw 15 % of a real 28-frame bracket). The
  focus-map base is now clamped so `base^p` cannot overflow, and a non-finite result aborts the run
  instead of being written. Regression tests: `test_full_contrast_edges_stay_finite`,
  `test_binary_noise_stays_finite_with_many_frames`.
- **One unregisterable frame misaligned all later frames**: the next frame was registered against the
  bad one. Registration now keeps the last good frame as the reference and rejects ECC correlations
  below 0.95 (real neighbours: 0.9991 – 1.0000; the reviewer's re-framed shots: 0.79 – 0.88).
- Re-run on the 11 real frames with the final code (uncompressed copies):

| Sharpness | Wall | Per frame | Peak RSS | Tile ratio median (p5 / p95) | Warnings |
|---|---|---|---|---|---|
| 2 (default, p = 16) | 10.2 s* | 0.93 s* | 3.1 GB | 0.98 (0.73 / 1.07) | none |
| 3 (p = 24) | 8.3 s* | 0.75 s* | 3.2 GB | 1.03 (0.87 / 1.12) | none |

  *Measured while the review's probes and test runs were still loading the machine; the quiet-machine
  figures above (0.5 – 0.8 s/frame) are the reference.

- Pure-black pixels: each source frame has 8 – 11 k (clipped shadows); the fused image has 6.7 k, of
  which ~1 k (0.004 %) are undershoot at the hardest edges where every source frame is bright. That
  is inherent to pyramid recombination and is not visible.
- Limitation of the quality metric: it compares the fused image with the *already warped* frames, so it
  cannot see softening caused by interpolation itself. Alignment uses bicubic, not Lanczos (spec §4.4).

## Installed copy, measured under load

Same 11 ZIP frames from the external SSD, through the *installed* venv and plugin and through the dev
venv, back to back, while Lightroom Classic was using ~100 % CPU (load average 12.7 / 15.4 / 10.1 on 8 cores):

| Run | Dev venv | Installed venv |
|---|---|---|
| 1 (cold file cache) | 107 s | 22.8 s |
| 2 | 16.7 s | 12.2 s |

The two copies are equivalent (the installed one was the faster in round 2); the spread is CPU
contention and cold reads, not the interpreter. Quiet-machine figures remain 0.5 – 0.8 s/frame;
expect 1 – 2 s/frame with Lightroom rendering at the same time.

## 22-frame stack from a real Lightroom run (alignment cascade)

A real 22-frame stack merged in Lightroom reported "alignment failed" on frames 12-22 and the
foreground (onion edges) showed doubled outlines. Cause, reproduced on the frames' embedded previews:
the camera was knocked 3.9 px between frames 11 and 12 and that single hop correlated at 0.927 (every
other neighbouring pair: >= 0.9995). It fell under the 0.95 gate; frames 13-22 were then each registered
against frame 11, moved further from it in focus, fell under the gate too and were merged with frame 11's
geometry: eleven frames blended misregistered.

Fix (tests: `test_bump_with_moderate_correlation_is_accepted`,
`test_rejected_hop_does_not_make_every_later_frame_fail`,
`test_bump_is_recovered_when_a_later_frame_matches_the_last_good_one`): a size-aware gate (0.80 for small
motion, 0.95 otherwise) and anchored, non-sticky registration (spec §4.4). After the fix the same 22
frames align with no warnings, the knock is recovered (+5 px step at frame 12) and the onion edges are
clean.

The "194 s, 8.8 s/frame" in that run's log was Lightroom rendering 22 edited RAFs (the engine waits for
frames); the engine now reports time waiting separately from time working.

## Hand-held light in frame (three consecutive frames, camera previews)

A hand and a white light occupy the left third of every frame and move between frames. Whole-frame
ECC (the earlier engine) bent the fit: **+8 px at the light, -6 px at the far table**, scene motion
< 1 px. With the moving region left out, the estimate matches the by-hand reference to 0.05 px
(device -0.83/+0.18, subject -0.62/+0.11, table -0.46/+0.05 against -0.91/+0.18, -0.65/+0.08,
-0.44/+0.01). The subject comes out ~7 % sharper (Laplacian variance 964 vs 902); the light stays
ghosted, by design.

Two failure modes found while testing this, both reproduced synthetically and now covered:

1. A strong moving edge bends the fit by 1-2 px *without* leaving outliers or lowering the
   correlation (the fit matches the object; low-contrast scene texture hides under the low-pass).
   Detected by disagreement with a contrast-normalised refinement: <= 0.12 px on every real hop,
   2-16 px with a moving box.
2. Lighting that differs between frames (a pool of light in a different place each frame): plain ECC
   returned warps 100-300 px off (13/15 hops) and its relative outlier test passed them. The
   normalised and log-high-pass fits were exact (0/15 failures, <= 0.07 px); all judging of a fit
   now happens in log-high-pass space. A smeared (motion-blurred) frame is the opposite case:
   there normalising made the fit worse (up to 21 px), so the choice is made by cost, not assumed.

Regression: the 11-frame stack and the 22-frame plate are unchanged (same geometry within 0.1 px,
zero warnings); 20 of 21 plate hops are on the unchanged fast path.
