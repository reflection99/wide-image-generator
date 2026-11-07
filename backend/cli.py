"""Command line entry: generate one ultra-wide image without the web UI.

Usage:
    python backend/cli.py --prompt "未来城市天际线，赛博朋克风格" --ratio 8 --out examples/8to1.png
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pipeline import ApiConfig, ImageClient, generate_wide_image

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Generate an ultra-wide image with segmented outpainting")
    p.add_argument("--prompt", required=True, help="scene description")
    p.add_argument("--ratio", type=float, default=8.0, help="target width/height ratio, e.g. 6 or 8")
    p.add_argument("--height", type=int, default=1024, help="final image height in px")
    p.add_argument("--model", default=os.getenv("IMAGE_MODEL", "gpt-image-2"))
    p.add_argument("--out", default="output.png", help="output png path")
    p.add_argument("--workdir", default="outputs/cli", help="dir for intermediate tiles")
    args = p.parse_args()

    cfg = ApiConfig(
        base_url=os.getenv("IMAGE_API_BASE_URL", "https://www.cnxmai.com"),
        api_key=os.getenv("IMAGE_API_KEY", ""),
        model=args.model,
        timeout=float(os.getenv("IMAGE_TIMEOUT", "300")),
        quality=os.getenv("IMAGE_QUALITY") or None,
    )
    t0 = time.time()

    def progress(done: int, total: int, img, msg: str) -> None:
        print(f"[{done}/{total}] {msg}  ({time.time() - t0:.0f}s)", flush=True)

    img = generate_wide_image(
        ImageClient(cfg),
        args.prompt,
        args.ratio,
        height=args.height,
        on_progress=progress,
        workdir=args.workdir,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"saved: {out.resolve()}  ({img.width}x{img.height}, {img.width / img.height:.2f}:1)")


if __name__ == "__main__":
    main()
