"""Segment generation + seam blending core logic.

Strategy: rolling outpainting.
1. Generate the first 1536x1024 tile from the user prompt.
2. Take the rightmost OVERLAP strip of the canvas, ask the image-edit API to
   continue the scene to the right, producing a new 1536x1024 tile.
3. Cross-fade the overlap region, append the rest of the new tile.
4. Repeat until the canvas reaches the target width.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx
from PIL import Image

TILE_W = 1536
TILE_H = 1024
OVERLAP = 512

CONTINUE_PROMPT = (
    "The attached image is the RIGHT EDGE crop of one section of an ultra-wide panoramic image. "
    "Continue the scene seamlessly to the RIGHT from this crop. "
    "CRITICAL RULES: keep the exact same art style, rendering technique, color grading, lighting "
    "direction, horizon height, scale and level of detail. The new content must look like the "
    "natural continuation of the same panorama - no borders, no frames, no text, no watermarks, "
    "no abrupt composition change, no repeated copies of the same foreground subject. "
    "Whole panorama subject: {prompt}"
)


@dataclass
class ApiConfig:
    base_url: str = "https://www.cnxmai.com"
    api_key: str = ""
    model: str = "gpt-image-2"
    timeout: float = 300.0
    quality: Optional[str] = None
    extra: dict = field(default_factory=dict)


class ImageApiError(RuntimeError):
    pass


class ImageClient:
    """Minimal OpenAI-compatible image client (generations + edits)."""

    def __init__(self, cfg: ApiConfig):
        self.cfg = cfg
        base = cfg.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        self._base = base

    # ---------- low level ----------
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.api_key}"}

    @staticmethod
    def _decode(payload: dict) -> Image.Image:
        data = (payload.get("data") or [{}])[0]
        b64 = data.get("b64_json")
        if b64:
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        url = data.get("url")
        if url:
            with httpx.Client(timeout=120) as c:
                r = c.get(url)
                r.raise_for_status()
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        raise ImageApiError(f"API 返回中没有图片数据: {str(payload)[:300]}")

    def _post_json(self, path: str, body: dict) -> Image.Image:
        if not self.cfg.api_key:
            raise ImageApiError("缺少 API Key，请在 .env 中配置 IMAGE_API_KEY")
        with httpx.Client(timeout=self.cfg.timeout) as c:
            r = c.post(f"{self._base}{path}", json=body, headers=self._headers())
        if r.status_code >= 400:
            raise ImageApiError(f"HTTP {r.status_code}: {r.text[:500]}")
        return self._decode(r.json())

    def _post_multipart(self, path: str, data: dict, files: dict) -> Image.Image:
        if not self.cfg.api_key:
            raise ImageApiError("缺少 API Key，请在 .env 中配置 IMAGE_API_KEY")
        with httpx.Client(timeout=self.cfg.timeout) as c:
            r = c.post(f"{self._base}{path}", data=data, files=files, headers=self._headers())
        if r.status_code >= 400:
            raise ImageApiError(f"HTTP {r.status_code}: {r.text[:500]}")
        return self._decode(r.json())

    # ---------- public ----------
    def generate(self, prompt: str, size: str = f"{TILE_W}x{TILE_H}") -> Image.Image:
        body = {"model": self.cfg.model, "prompt": prompt, "n": 1, "size": size}
        if self.cfg.quality:
            body["quality"] = self.cfg.quality
        return self._post_json("/images/generations", body)

    def edit(self, image: Image.Image, prompt: str, size: str = f"{TILE_W}x{TILE_H}") -> Image.Image:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        data = {"model": self.cfg.model, "prompt": prompt, "size": size}
        if self.cfg.quality:
            data["quality"] = self.cfg.quality
        files = {"image": ("reference.png", buf.read(), "image/png")}
        try:
            return self._post_multipart("/images/edits", data, files)
        except ImageApiError as exc:
            # Some relay deployments reject the edits endpoint. Re-raise with a clear hint.
            raise ImageApiError(f"images/edits 调用失败: {exc}") from exc


def build_first_prompt(user_prompt: str, ratio: float) -> str:
    return (
        f"Generate the LEFTMOST section of a seamless ultra-wide panoramic image "
        f"(final canvas aspect ratio about {ratio:g}:1). This panel will later be extended "
        f"to the right, so compose it as the left part of a continuous scene: keep the horizon "
        f"line stable, avoid a centered closed composition, and let visual elements flow "
        f"naturally toward the right edge. Whole panorama subject: {user_prompt}. "
        f"Cinematic wide-angle composition, consistent lighting, high detail, "
        f"no borders, no frames, no text, no watermark."
    )


def _gradient_mask(size: tuple[int, int]) -> Image.Image:
    """Left->right linear alpha ramp (0 at left edge, 255 at right edge)."""
    w, h = size
    ramp = Image.new("L", (w, 1))
    ramp.putdata([int(255 * x / max(1, w - 1)) for x in range(w)])
    return ramp.resize((w, h))


def blend_tiles(canvas: Image.Image, tile: Image.Image, overlap: int = OVERLAP) -> Image.Image:
    """Attach `tile` to the right edge of `canvas`, cross-fading the overlap region."""
    cw, ch = canvas.size
    tw, th = tile.size
    overlap = min(overlap, cw, tw)
    out = Image.new("RGB", (cw - overlap + tw, max(ch, th)), (0, 0, 0))
    out.paste(canvas.convert("RGB"), (0, 0))

    left = canvas.crop((cw - overlap, 0, cw, th)).convert("RGB")
    right = tile.crop((0, 0, overlap, th)).convert("RGB")
    merged = Image.composite(right, left, _gradient_mask((overlap, th)))
    out.paste(merged, (cw - overlap, 0))
    out.paste(tile.crop((overlap, 0, tw, th)).convert("RGB"), (cw, 0))
    return out


ProgressCb = Callable[[int, int, Image.Image, str], None]


def normalize_tile(img: Image.Image, size: tuple[int, int] = (TILE_W, TILE_H)) -> Image.Image:
    """Force an API output to the canonical tile size without distortion.

    Some relays/models ignore the requested `size` (e.g. return 3:1 for a wide
    prompt). We center-crop to the target aspect ratio, then resize.
    """
    tw, th = size
    img = img.convert("RGB")
    if img.size == (tw, th):
        return img
    target_ar, ar = tw / th, img.width / img.height
    if ar > target_ar:  # too wide -> crop width (keep center)
        new_w = int(round(img.height * target_ar))
        x0 = (img.width - new_w) // 2
        img = img.crop((x0, 0, x0 + new_w, img.height))
    elif ar < target_ar:  # too tall -> crop height (keep center)
        new_h = int(round(img.width / target_ar))
        y0 = (img.height - new_h) // 2
        img = img.crop((0, y0, img.width, y0 + new_h))
    return img.resize((tw, th), Image.LANCZOS)


def generate_wide_image(
    client: ImageClient,
    user_prompt: str,
    ratio: float,
    height: int = TILE_H,
    on_progress: Optional[ProgressCb] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    workdir: Optional[str] = None,
) -> Image.Image:
    """Generate an ultra-wide image whose width/height ratio == `ratio`."""
    if ratio <= 0:
        raise ValueError("ratio must be positive")

    target_w = int(round(height * ratio))
    # number of tiles needed given each tile adds (TILE_W - OVERLAP) after the first
    step = TILE_W - OVERLAP
    total_tiles = max(1, 1 + ((target_w - TILE_W) + step - 1) // step)

    if on_progress:
        on_progress(0, total_tiles, None, "生成首块画面…")

    canvas = normalize_tile(client.generate(build_first_prompt(user_prompt, ratio)))
    if workdir:
        _save(canvas, os.path.join(workdir, "tile_0.png"))
    if on_progress:
        on_progress(1, total_tiles, canvas, f"图块 1/{total_tiles} 完成")

    for i in range(1, total_tiles):
        if should_stop and should_stop():
            break
        strip = canvas.crop((canvas.width - OVERLAP, 0, canvas.width, TILE_H))
        prompt = CONTINUE_PROMPT.format(prompt=user_prompt)
        tile = normalize_tile(client.edit(strip, prompt))
        canvas = blend_tiles(canvas, tile, OVERLAP)
        if workdir:
            _save(tile, os.path.join(workdir, f"tile_{i}.png"))
            _save(canvas, os.path.join(workdir, "canvas_progress.png"))
        if on_progress:
            on_progress(i + 1, total_tiles, canvas, f"图块 {i + 1}/{total_tiles} 完成")

    # final exact size
    canvas = canvas.crop((0, 0, min(target_w, canvas.width), height))
    return canvas


def _save(img: Image.Image, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
