"""SnapClip 2API 网关 (demo)

双模运行:
  real : 真实调用 ai-video.thebetter.ai（匿名账号池，每账号每日 3 次免费额度）
  mock : 完全本地伪造，用于压测 demo 自身性能，不触碰上游

启动:
  python server.py --port 8940 --mode mock
  python server.py --port 8940 --mode real

OpenAI 风格接口:
  GET  /v1/models
  POST /v1/videos/generations      {"model":"snapclip/magic","image":"<url|b64>","theme_id":"...","theme_item_id":"...","mode":"free|vidu|kling|alibaba|pixverse"}
  GET  /v1/videos/generations/{id}

原生接口（透传上游语义）:
  GET  /api/v1/api_meta
  GET  /api/v1/themes?category_id=&page=&size=
  GET  /api/v1/categories
  GET  /api/v1/accounts       账号池状态
  GET  /health /stats
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from snapclip_client import BASE, SAAS, SnapClipClient

ROOT = Path(__file__).parent
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)
ACCOUNT_FILE = ROOT / "accounts.json"

FREE_MODES = {"free", "GENERATE"}
PRO_MODES = {"vidu": "GENERATE_VIDU", "kling": "GENERATE_KLING",
             "alibaba": "GENERATE_ALIBABA", "pixverse": "GENERATE_PIXVERSE"}

START_TS = time.time()
STATS = {"submit": 0, "submit_ok": 0, "submit_err": 0, "mock": 0, "real": 0, "pool_rotate": 0}
TASKS: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=6)
_lock = threading.Lock()


class AccountPool:
    """匿名账号池：每个 secret 对应一个 uid，每日 3 次免费额度"""

    def __init__(self, size: int = 4):
        self.secrets: list[str] = []
        self.tokens: dict[str, str] = {}
        self.quota: dict[str, int] = {}
        self._load(size)

    def _load(self, size: int) -> None:
        if ACCOUNT_FILE.exists():
            data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
            self.secrets = data.get("secrets", [])
        while len(self.secrets) < size:
            self.secrets.append(uuid.uuid4().hex)
        ACCOUNT_FILE.write_text(json.dumps({"secrets": self.secrets}, ensure_ascii=False, indent=1), encoding="utf-8")

    def _ensure(self, secret: str) -> str:
        if secret not in self.tokens:
            self.tokens[secret] = _anon(secret)
            self.quota[secret] = _count(self.tokens[secret])
        return self.tokens[secret]

    def acquire(self) -> tuple[str, str]:
        """返回 (secret, token)，优先选择额度未耗尽的账号"""
        order = sorted(self.secrets, key=lambda s: (-self.quota.get(s, 0), s))
        for secret in order:
            try:
                token = self._ensure(secret)
            except Exception:  # noqa: BLE001
                continue
            if self.quota.get(secret, 0) > 0:
                return secret, token
        self.secrets.append(uuid.uuid4().hex)
        ACCOUNT_FILE.write_text(json.dumps({"secrets": self.secrets}, ensure_ascii=False, indent=1), encoding="utf-8")
        secret = self.secrets[-1]
        STATS["pool_rotate"] += 1
        return secret, self._ensure(secret)

    def consume(self, secret: str) -> None:
        self.quota[secret] = max(0, self.quota.get(secret, 1) - 1)

    def snapshot(self) -> list[dict]:
        out = []
        for s in self.secrets:
            out.append({"secret": s[:8] + "…", "has_token": s in self.tokens, "quota": self.quota.get(s)})
        return out


def _anon(secret: str) -> str:
    resp = requests.post(
        f"{SAAS}/auth/api/v1/tokens/provider/secret",
        headers={"X-APP-ID": "ai-video", "Content-Type": "application/json"},
        json={"provider": "anonymous", "secret": secret},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["data"]["token"]


def _count(token: str) -> int:
    resp = requests.get(f"{BASE}/v1/user/daily_free_count",
                        headers={"X-APP-ID": "ai-video", "X-ACCESS-TOKEN": token}, timeout=20)
    return int(resp.json().get("data", {}).get("count", 0))


POOL: AccountPool | None = None
MODE = "mock"


class GenRequest(BaseModel):
    model: str = "snapclip/magic"
    image: str = ""
    theme_id: str = "vid_viral_comic_chaos_260527"
    theme_item_id: str = "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4"
    mode: str = "free"
    wait: bool = False


app = FastAPI(title="SnapClip 2API", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mode": MODE, "uptime": round(time.time() - START_TS, 1), "tasks": len(TASKS)}


@app.get("/stats")
def stats() -> dict:
    return {**STATS, "accounts": len(POOL.secrets) if POOL else 0,
            "uptime": round(time.time() - START_TS, 1)}


@app.get("/v1/models")
def models() -> dict:
    apis = ["emote", "hug", "magic", "beastify", "transform", "video_swap",
            "dance", "harry_potter", "figure", "step", "chain"]
    return {"object": "list", "data": [
        {"id": f"snapclip/{a}", "object": "model", "owned_by": "thebetter.ai"} for a in apis
    ]}


@app.get("/api/v1/categories")
def categories() -> JSONResponse:
    if MODE == "mock":
        return JSONResponse({"code": 0, "data": {"list": [
            {"categoryId": "vid_viral_comic_chaos_260527", "name": "Comic Chaos"},
            {"categoryId": "vid_viral_viral_dances_260611", "name": "Viral Dances"},
        ]}})
    secret, token = POOL.acquire()
    return JSONResponse({"code": 0, "data": {"list": SnapClipClient(token=token).categories()}})


@app.get("/api/v1/themes")
def themes(category_id: str | None = None, page: int = 1, size: int = 20) -> JSONResponse:
    if MODE == "mock":
        return JSONResponse({"code": 0, "data": {"list": [_mock_item()], "pageInfo": {"more": False, "totalCount": 1}}})
    _, token = POOL.acquire()
    data = SnapClipClient(token=token).theme_images(category_id, page=page, size=size)
    return JSONResponse({"code": 0, "data": data})


@app.get("/api/v1/accounts")
def accounts() -> dict:
    return {"mode": MODE, "accounts": POOL.snapshot() if POOL else []}


def _mock_item() -> dict:
    return {"themeId": "vid_viral_comic_chaos_260527",
            "themeItemId": "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4",
            "name": "Chibi Me", "apiId": "magic_api", "inputCount": 1, "restrictedType": "FREE_USER",
            "generateModeList": [{"modeType": "GENERATE_VIDU", "restrictedType": "PRO_USER", "pointPrice": 100}]}


def _decode_image(image: str) -> tuple[str, bool]:
    """支持 data-uri / b64 / 本地路径 / http url；返回 (路径, 是否为临时文件)

    临时文件由后台任务负责清理，本地路径原文件绝不删除。
    """
    if not image:
        raise HTTPException(400, "image required (url | b64 | data-uri | local path)")
    path: Path
    if image.startswith("http://") or image.startswith("https://"):
        path = WORK / f"{uuid.uuid4().hex}.jpg"
        with requests.get(image, timeout=60, stream=True) as resp:
            resp.raise_for_status()
            path.write_bytes(resp.content)
        return str(path), True
    if image.startswith("data:"):
        raw = base64.b64decode(image.split(",", 1)[1])
        path = WORK / f"{uuid.uuid4().hex}.jpg"
        path.write_bytes(raw)
        return str(path), True
    if os.path.exists(image):
        return image, False
    try:
        raw = base64.b64decode(image, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"unsupported image input: {exc}") from exc
    path = WORK / f"{uuid.uuid4().hex}.jpg"
    path.write_bytes(raw)
    return str(path), True


def _run_task(task_id: str, req: GenRequest, img_path: str, temp_image: bool = False) -> None:
    task = TASKS[task_id]
    t0 = time.time()
    if MODE == "mock":
        time.sleep(1.2)
        task.update(status="SUCCESS", progress=100,
                    url=f"/api/v1/files/{task_id}.mp4", elapsed=round(time.time() - t0, 2))
        (WORK / f"{task_id}.mp4").write_bytes(b"\x00\x00\x00 ftypisommock")
        STATS["mock"] += 1
        return
    api = req.model.split("/", 1)[1] if "/" in req.model else req.model
    is_free = req.mode in FREE_MODES
    mode_type = "GENERATE" if is_free else PRO_MODES.get(req.mode, "GENERATE")
    restricted = "FREE_USER" if is_free else "PRO_USER"
    secret, token = POOL.acquire()
    client = SnapClipClient(token=token)
    try:
        bucket, key = client.upload_image(img_path)
        task["progress"] = 20
        upstream_id = client.submit(api, [key], req.theme_id, req.theme_item_id,
                                    bucket=bucket, restricted_type=restricted, mode_type=mode_type)
        task.update(upstream_id=upstream_id, progress=35)
        data = client.poll(upstream_id, timeout_s=420, interval_s=3)
        task.update(upstream_id=upstream_id, progress=90)
        if data.get("status") != "SUCCESS":
            task.update(status="FAILED", error=data.get("failedReason") or "upstream failed",
                        elapsed=round(time.time() - t0, 2))
            with _lock:
                STATS["submit_err"] += 1
            return
        dest = WORK / f"{task_id}.mp4"
        client.download(data, str(dest))
        POOL.consume(secret)
        task.update(status="SUCCESS", progress=100, url=f"/api/v1/files/{task_id}.mp4",
                    elapsed=round(time.time() - t0, 2), used_point=data.get("usedPoint"),
                    upstream_s3=data.get("s3Key"))
        with _lock:
            STATS["submit_ok"] += 1
            STATS["real"] += 1
    except Exception as exc:  # noqa: BLE001
        with _lock:
            STATS["submit_err"] += 1
        task.update(status="FAILED", error=str(exc)[:300], elapsed=round(time.time() - t0, 2))
    finally:
        if temp_image:
            try:
                os.unlink(img_path)
            except OSError:
                pass


@app.post("/v1/videos/generations")
async def generate(req: GenRequest) -> dict:
    task_id = f"vid_{uuid.uuid4().hex}"
    with _lock:
        STATS["submit"] += 1
    TASKS[task_id] = {"id": task_id, "status": "PENDING", "progress": 0,
                      "created": int(time.time()), "model": req.model, "mode": req.mode}
    if MODE == "mock":
        _executor.submit(_run_task, task_id, req, "")
    else:
        img, temp = _decode_image(req.image)
        _executor.submit(_run_task, task_id, req, img, temp)
    if req.wait:
        for _ in range(600):
            if TASKS[task_id]["status"] in ("SUCCESS", "FAILED"):
                break
            await asyncio.sleep(1)
    return {"id": task_id, **TASKS[task_id], "object": "video.generation"}


@app.get("/v1/videos/generations/{task_id}")
def get_task(task_id: str) -> dict:
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return {"id": task_id, "object": "video.generation", **task}


@app.get("/api/v1/files/{name}")
def get_file(name: str) -> FileResponse:
    path = WORK / name
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/demo")
def demo() -> FileResponse:
    return FileResponse(ROOT / "demo.html", media_type="text/html")


def main() -> None:
    global POOL, MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8940)
    ap.add_argument("--mode", choices=["mock", "real"], default="mock")
    ap.add_argument("--pool", type=int, default=4)
    args = ap.parse_args()
    MODE = args.mode
    POOL = AccountPool(size=args.pool)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
