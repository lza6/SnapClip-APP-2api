"""SnapClip 端到端链路探测：匿名 token -> S3 上传 -> 提交任务 -> 轮询结果"""
import json
import sys
import time
import uuid

import boto3
import requests

APP = "ai-video"
BASE = "https://ai-video.thebetter.ai"
SAAS = "https://saas.castbox.fm"


def anon_token(secret: str | None = None) -> str:
    r = requests.post(
        f"{SAAS}/auth/api/v1/tokens/provider/secret",
        headers={"X-APP-ID": APP, "Content-Type": "application/json"},
        json={"provider": "anonymous", "secret": secret or uuid.uuid4().hex},
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (0, None) or "data" not in body:
        raise RuntimeError(f"token failed: {body}")
    return body["data"]["token"]


def hdr(tok: str) -> dict:
    return {"X-APP-ID": APP, "X-ACCESS-TOKEN": tok, "Content-Type": "application/json"}


def upload_image(tok: str, path: str) -> dict:
    r = requests.get(f"{BASE}/v1/token/generate_image_token", params={"count": 1}, headers=hdr(tok), timeout=20)
    r.raise_for_status()
    d = r.json()["data"]
    s3 = boto3.client(
        "s3",
        aws_access_key_id=d["accessKeyId"],
        aws_secret_access_key=d["secretAccessKey"],
        aws_session_token=d["sessionToken"],
        region_name="us-east-1",
    )
    key = d["imageS3KeyList"][0]
    s3.upload_file(path, d["S3bucket"], key, ExtraArgs={"ContentType": "image/jpeg"})
    return {"bucket": d["S3bucket"], "key": key}


def submit(tok: str, api_id: str, bucket: str, key: str, theme_id: str, theme_item_id: str) -> str:
    r = requests.post(
        f"{BASE}/v1/task/{api_id}",
        headers=hdr(tok),
        json={
            "s3_bucket": bucket,
            "s3_key_list": [key],
            "theme_id": theme_id,
            "theme_item_id": theme_item_id,
            "restricted_type": "FREE_USER",
            "mode_type": "GENERATE",
        },
        timeout=30,
    )
    print("submit:", r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json()["data"]["taskId"]


def poll(tok: str, task_id: str, timeout_s: int = 240) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        r = requests.get(f"{BASE}/v1/task/task_result", params={"task_id": task_id}, headers=hdr(tok), timeout=30)
        d = r.json().get("data") or {}
        if d != last:
            print(f"  [{int(time.time()-t0)}s] status={d.get('status')} used={d.get('usedPoint')} reason={d.get('failedReason')}")
            last = d
        if d.get("status") in ("SUCCESS", "FAILED"):
            return d
        time.sleep(3)
    raise TimeoutError("poll timeout")


if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "D:/apk-analysis/photify/face.jpg"
    theme_id = sys.argv[2] if len(sys.argv) > 2 else "vid_gaze_heart_vibe_g_260227"
    theme_item = sys.argv[3] if len(sys.argv) > 3 else "vid_gaze_heart_vibe_g_260227/04_heart_cute_heart_260227.mp4"
    api_id = sys.argv[4] if len(sys.argv) > 4 else "magic"

    tok = anon_token()
    print("token ok", tok[:24], "...")
    up = upload_image(tok, img)
    print("uploaded:", up)
    tid = submit(tok, api_id, up["bucket"], up["key"], theme_id, theme_item)
    print("taskId:", tid)
    res = poll(tok, tid)
    print("RESULT:", json.dumps(res, ensure_ascii=False)[:1200])
