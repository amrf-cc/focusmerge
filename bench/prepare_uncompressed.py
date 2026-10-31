"""Re-write frames as uncompressed TIFFs (what the plugin's export produces) into a scratch folder."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "FocusMerge.lrdevplugin", "engine"))
from fm import tiffio  # noqa: E402


def main(dest, sources):
    os.makedirs(dest, exist_ok=True)
    for src in sources:
        t = time.perf_counter()
        arr = tiffio.read_rgb16(src)
        out = os.path.join(dest, os.path.basename(src))
        how = tiffio.write_result(src, out, arr)      # ZIP template -> fresh uncompressed + metadata
        print(f"{os.path.basename(src)}: read+write {time.perf_counter() - t:.2f}s ({how})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
