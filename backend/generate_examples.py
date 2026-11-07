"""Generate the deliverable samples (6:1 and 8:1) into examples/.

Run:
    python backend/generate_examples.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pipeline import ApiConfig, ImageClient, generate_wide_image

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

CASES = [
    (6.0, "samples/6to1_city.png",
     "黄昏时分的未来城市天际线全景，赛博朋克霓虹灯牌与薄雾，湿润街道反光，电影级光影，超广角"),
    (8.0, "samples/8to1_mountain.png",
     "清晨雪山与高山湖泊的超宽全景，湖面倒影，晨雾弥漫，金色阳光从左侧山脊洒下，写实摄影风格"),
]


def main() -> None:
    load_dotenv(ROOT / ".env")
    cfg = ApiConfig(
        base_url=os.getenv("IMAGE_API_BASE_URL", "https://www.cnxmai.com"),
        api_key=os.getenv("IMAGE_API_KEY", ""),
        model=os.getenv("IMAGE_MODEL", "gpt-image-2"),
        timeout=float(os.getenv("IMAGE_TIMEOUT", "300")),
    )
    client = ImageClient(cfg)
    EXAMPLES.mkdir(exist_ok=True)

    for ratio, name, prompt in CASES:
        print(f"=== {ratio:g}:1 -> {name} ===", flush=True)
        t0 = time.time()

        def progress(done, total, img, msg, _t0=t0):
            print(f"  [{done}/{total}] {msg} ({time.time() - _t0:.0f}s)", flush=True)

        out = generate_wide_image(
            client, prompt, ratio,
            on_progress=progress,
            workdir=str(EXAMPLES / "tiles" / Path(name).stem),
        )
        path = EXAMPLES / name
        path.parent.mkdir(parents=True, exist_ok=True)
        out.save(path)
        print(f"  saved {path} ({out.width}x{out.height}, {out.width / out.height:.2f}:1)", flush=True)


if __name__ == "__main__":
    main()
