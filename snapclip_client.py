"""SnapClip 1.1.1 (ai.video.photo.animator.face.dance.animation) 协议客户端

链路:
  1) POST https://saas.castbox.fm/auth/api/v1/tokens/provider/secret
     body {"provider":"anonymous","secret":"<任意随机串>"}  -> X-ACCESS-TOKEN(JWT)
  2) GET  /v1/token/generate_image_token?count=N          -> S3 临时凭证(imageS3KeyList)
  3) PUT  s3://cre8vid-user/<key>                          -> 直传用户图片
  4) POST /v1/task/<api_id>                                -> taskId
  5) GET  /v1/task/task_result?task_id=                    -> s3Key + 结果凭证
  6) GET  s3://cre8vid-user/<s3Key>                        -> 成品 mp4
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import boto3
import requests

APP_ID = "ai-video"
BASE = "https://ai-video.thebetter.ai"
SAAS = "https://saas.castbox.fm"
REGION = "us-east-1"


@dataclass
class TaskResult:
    status: str
    video: bytes | None
    raw: dict
    elapsed: float


class SnapClipClient:
    def __init__(self, token: str | None = None, app_id: str = APP_ID):
        self.app_id = app_id
        self.token = token or self.anonymous_token()

    # ---------- auth ----------
    def anonymous_token(self, secret: str | None = None) -> str:
        resp = requests.post(
            f"{SAAS}/auth/api/v1/tokens/provider/secret",
            headers={"X-APP-ID": self.app_id, "Content-Type": "application/json"},
            json={"provider": "anonymous", "secret": secret or uuid.uuid4().hex},
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("data", {}).get("token"):
            raise RuntimeError(f"anonymous token failed: {body}")
        return body["data"]["token"]

    @property
    def _headers(self) -> dict:
        return {"X-APP-ID": self.app_id, "X-ACCESS-TOKEN": self.token}

    def _json_headers(self) -> dict:
        return {**self._headers, "Content-Type": "application/json"}

    def _get(self, path: str, **params) -> dict:
        resp = requests.get(f"{BASE}{path}", params=params or None, headers=self._headers, timeout=30)
        return self._unwrap(resp)

    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(f"{BASE}{path}", json=payload, headers=self._json_headers(), timeout=30)
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: requests.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError(f"upstream {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400 or body.get("code") not in (0, None):
            raise RuntimeError(f"upstream {resp.status_code} code={body.get('code')}: {body.get('message')}")
        return body.get("data") or {}

    # ---------- quota ----------
    def daily_free_count(self) -> int:
        return int(self._get("/v1/user/daily_free_count").get("count", 0))

    # ---------- catalog ----------
    def api_meta(self) -> list[dict]:
        resp = requests.get(f"{BASE}/v1/api_meta", timeout=20)
        return resp.json()["data"]["apiList"]

    def categories(self) -> list[dict]:
        return self._get("/v1/task/theme_category_list")["list"]

    def theme_images(self, category_id: str | None = None, page: int = 1, size: int = 20) -> dict:
        if category_id:
            return self._get("/v1/task/theme_image_list_by_category", categoryId=category_id, page=page, size=size)
        return self._get("/v1/task/theme_image_hot_list", page=page, size=size)

    def theme_info(self, theme_id: str, theme_item_id: str) -> dict:
        return self._get("/v1/task/theme_image_info", themeId=theme_id, themeItemId=theme_item_id)

    # ---------- pipeline ----------
    def upload_image(self, path: str) -> tuple[str, str]:
        data = self._get("/v1/token/generate_image_token", count=1)
        bucket = data["S3bucket"]
        key = data["imageS3KeyList"][0]
        s3 = boto3.client(
            "s3",
            aws_access_key_id=data["accessKeyId"],
            aws_secret_access_key=data["secretAccessKey"],
            aws_session_token=data["sessionToken"],
            region_name=REGION,
        )
        s3.upload_file(path, bucket, key, ExtraArgs={"ContentType": "image/jpeg"})
        return bucket, key

    def submit(self, api_id: str, image_keys: list[str], theme_id: str, theme_item_id: str,
               bucket: str | None = None, restricted_type: str = "FREE_USER", mode_type: str = "GENERATE") -> str:
        payload = {
            "s3_key_list": image_keys,
            "theme_id": theme_id,
            "theme_item_id": theme_item_id,
            "restricted_type": restricted_type,
            "mode_type": mode_type,
        }
        if bucket:
            payload["s3_bucket"] = bucket
        return self._post(f"/v1/task/{api_id}", payload)["taskId"]

    def chain(self, prev_task_id: str, theme_id: str, theme_item_id: str,
              restricted_type: str = "FREE_USER", mode_type: str = "GENERATE") -> str:
        return self._post("/v1/task/chain", {
            "task_id": prev_task_id,
            "theme_id": theme_id,
            "theme_item_id": theme_item_id,
            "restricted_type": restricted_type,
            "mode_type": mode_type,
        })["taskId"]

    def poll(self, task_id: str, timeout_s: int = 300, interval_s: float = 3.0) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            data = self._get("/v1/task/task_result", task_id=task_id)
            if data.get("status") in ("SUCCESS", "FAILED"):
                return data
            time.sleep(interval_s)
        raise TimeoutError(f"task {task_id} not finished in {timeout_s}s")

    @staticmethod
    def download(data: dict, dest: str) -> str:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=data["accessKeyId"],
            aws_secret_access_key=data["secretAccessKey"],
            aws_session_token=data["sessionToken"],
            region_name=REGION,
        )
        s3.download_file(data["s3Bucket"], data["s3Key"], dest)
        return dest

    def generate(self, image_path: str, theme_id: str, theme_item_id: str, api_id: str = "magic",
                 dest: str | None = None, **submit_kwargs) -> TaskResult:
        t0 = time.time()
        bucket, key = self.upload_image(image_path)
        task_id = self.submit(api_id, [key], theme_id, theme_item_id, bucket=bucket, **submit_kwargs)
        data = self.poll(task_id)
        video = None
        if data.get("status") == "SUCCESS" and data.get("s3Key"):
            tmp = dest or f"{task_id}.mp4"
            self.download(data, tmp)
            with open(tmp, "rb") as fh:
                video = fh.read()
        return TaskResult(status=data.get("status", "UNKNOWN"), video=video, raw=data, elapsed=time.time() - t0)


if __name__ == "__main__":
    import sys

    img = sys.argv[1] if len(sys.argv) > 1 else "D:/apk-analysis/photify/face.jpg"
    theme_id = sys.argv[2] if len(sys.argv) > 2 else "vid_viral_comic_chaos_260527"
    theme_item = sys.argv[3] if len(sys.argv) > 3 else "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4"
    api_id = sys.argv[4] if len(sys.argv) > 4 else "magic"

    c = SnapClipClient()
    print("token:", c.token[:32], "...")
    print("daily free:", c.daily_free_count())
    res = c.generate(img, theme_id, theme_item, api_id=api_id, dest="out_sample.mp4")
    print("status:", res.status, "elapsed: %.1fs" % res.elapsed, "bytes:", len(res.video or b""))
    print("raw:", {k: v for k, v in res.raw.items() if k in ("s3Key", "usedPoint", "createdAt", "finishedAt")})
