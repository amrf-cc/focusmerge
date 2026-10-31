# Third-party software

FocusMerge's own source code is released under the MIT License (see `LICENSE`).

FocusMerge **does not contain** the libraries below. The installer downloads them onto the
installing computer from the Python Package Index (PyPI) and, if the computer has no suitable
Python, downloads a Python interpreter and the `uv` manager from their publishers. Each package
carries its own license files, which are installed alongside it
(`~/Library/Application Support/FocusMerge/venv/lib/python3.*/site-packages/`).

| Component | Version used | License | Notes |
|---|---|---|---|
| [NumPy](https://numpy.org) | 2.4.x | BSD-3-Clause (plus 0BSD, MIT, Zlib, CC0 for parts) | array maths |
| [opencv-python-headless](https://github.com/opencv/opencv-python) | 5.0.x | Apache-2.0 | image registration and pyramids. The wheel redistributes FFmpeg (LGPL) and other libraries; see `cv2/LICENSE-3RD-PARTY.txt` inside the installed package |
| [tifffile](https://github.com/cgohlke/tifffile) | 2026.x | BSD-3-Clause | TIFF reading and writing |
| [imagecodecs](https://github.com/cgohlke/imagecodecs) | 2026.x | BSD-3-Clause | TIFF compression codecs; the wheel bundles several codec libraries, each licensed in `imagecodecs/licenses/` |
| [Python](https://www.python.org) (only if downloaded) | 3.12 | PSF License | via python-build-standalone |
| [uv](https://github.com/astral-sh/uv) (only if downloaded) | latest | MIT or Apache-2.0 | used once to fetch Python |
| [ExifTool](https://exiftool.org) (optional, never bundled) | any | Perl Artistic / GPL | used to copy metadata when the user already has it installed |
| Adobe Lightroom Classic SDK | 6.0+ | Adobe terms | the plugin is written against Adobe's public SDK; Adobe and Lightroom are trademarks of Adobe Inc. |
