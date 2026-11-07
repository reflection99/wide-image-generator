"""FastAPI service: job API + static demo hosting."""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .pipeline import ApiConfig, ImageApiError, ImageClient, generate_wide_image

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(exist_ok=True)
STATIC = ROOT / "static"
EXAMPLES = ROOT / "examples"

MODELS = ["gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-1.5", "gpt-image-1"]

app = FastAPI(title="Wide Image Generator", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    ratio: float = Field(8.0, gt=0.5, le=16)
    model: Optional[str] = None
    height: int = Field(1024, ge=512, le=1536)


def _update(job_id: str, **kw) -> None:
    with _lock:
        _jobs[job_id].update(kw)


def _run_job(job_id: str, req: GenerateRequest) -> None:
    cfg = ApiConfig(
        base_url=os.getenv("IMAGE_API_BASE_URL", "https://www.cnxmai.com"),
        api_key=os.getenv("IMAGE_API_KEY", ""),
        model=req.model or os.getenv("IMAGE_MODEL", "gpt-image-2"),
        timeout=float(os.getenv("IMAGE_TIMEOUT", "300")),
        quality=os.getenv("IMAGE_QUALITY") or None,
    )
    workdir = OUTPUTS / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    def on_progress(done: int, total: int, img, message: str) -> None:
        payload = {
            "step": done,
            "total": total,
            "message": message,
            "elapsed": round(time.time() - _jobs[job_id]["started"], 1),
        }
        if img is not None:
            snap = workdir / f"progress_{done}.png"
            img.save(snap)
            payload["preview"] = f"/outputs/{job_id}/{snap.name}"
        _update(job_id, **payload)

    try:
        _update(job_id, status="running", message="准备调用 API…")
        client = ImageClient(cfg)
        img = generate_wide_image(
            client,
            req.prompt,
            req.ratio,
            height=req.height,
            on_progress=on_progress,
            workdir=str(workdir),
        )
        out = workdir / "result.png"
        img.save(out)
        _update(
            job_id,
            status="done",
            image=f"/outputs/{job_id}/result.png",
            width=img.width,
            height=img.height,
            message="生成完成",
            elapsed=round(time.time() - _jobs[job_id]["started"], 1),
        )
    except Exception as exc:  # noqa: BLE001 - surface the error to the UI
        _update(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            message="生成失败",
            elapsed=round(time.time() - _jobs[job_id]["started"], 1),
        )


@app.post("/api/generate")
def create_job(req: GenerateRequest):
    if not os.getenv("IMAGE_API_KEY"):
        raise HTTPException(500, "服务端未配置 IMAGE_API_KEY")
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "step": 0,
            "total": 0,
            "message": "排队中",
            "prompt": req.prompt,
            "ratio": req.ratio,
            "started": time.time(),
            "previews": [],
        }
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return dict(job)


@app.get("/api/models")
def get_models():
    return {"models": MODELS, "default": os.getenv("IMAGE_MODEL", MODELS[0])}


@app.get("/api/health")
def health():
    return {"ok": True, "api_key_configured": bool(os.getenv("IMAGE_API_KEY"))}


app.mount("/outputs", StaticFiles(directory=str(OUTPUTS)), name="outputs")
if EXAMPLES.exists():
    app.mount("/examples", StaticFiles(directory=str(EXAMPLES)), name="examples")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))
