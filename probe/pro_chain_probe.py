"""PRO 门控 / step 双图 / chain 续接 语义探测"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from snapclip_client import BASE, SnapClipClient

OUT = Path(__file__).parent / "pro_chain_probe.json"
FACE1 = "D:/apk-analysis/photify/test_face.jpg"
FACE2 = "D:/apk-analysis/photify/A_out.jpg"
STEP_THEME = "video_shift_g_250717"
STEP_ITEM = "video_shift_g_250717/0006_shift_youth_250717.mp4"
MAGIC_THEME = "vid_viral_comic_chaos_260527"
MAGIC_ITEM = "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4"

log: dict = {"pro": [], "step": {}, "chain": {}}


def main() -> None:
    c = SnapClipClient()
    print("token", c.token[:20], "| free:", c.daily_free_count())
    hdr = {"X-APP-ID": "ai-video", "X-ACCESS-TOKEN": c.token, "Content-Type": "application/json"}

    # ---- 1) PRO 门控：是否只由 restricted_type 决定 ----
    device_payloads = [
        {},
        {"androidId": "9774d56d682e549c", "brand": "google", "model": "Pixel 7", "osVersion": "14"},
        {"deviceId": "9774d56d682e549c", "deviceType": "android", "androidId": "9774d56d682e549c"},
    ]
    for extra in device_payloads:
        headers = dict(hdr)
        if extra:
            headers["X-DEVICE-INFO"] = json.dumps(extra)
        r = requests.post(f"{BASE}/v1/task/magic", headers=headers, timeout=30, json={
            "s3_key_list": ["probe.jpg"], "theme_id": MAGIC_THEME, "theme_item_id": MAGIC_ITEM,
            "restricted_type": "PRO_USER", "mode_type": "GENERATE_VIDU",
        })
        log["pro"].append({"device_info": extra or None, "status": r.status_code, "body": r.text[:200]})
        print("PRO", extra or "(no device header)", "->", r.status_code, r.text[:120])

    # ---- 2) step 双图 ----
    imgs = [c.upload_image(FACE1), c.upload_image(FACE2)]
    print("uploaded2:", imgs)
    task_id = c.submit("step", [k for _, k in imgs], STEP_THEME, STEP_ITEM, bucket=imgs[0][0])
    print("step taskId:", task_id)
    data = c.poll(task_id, timeout_s=420, interval_s=4)
    log["step"] = {"task_id": task_id, "images": [i[1] for i in imgs],
                   "result": {k: v for k, v in data.items()
                              if k in ("status", "failedReason", "s3Key", "usedPoint", "createdAt", "finishedAt")}}
    print("step result:", log["step"]["result"])
    if data.get("status") == "SUCCESS":
        dest = "step_out.mp4"
        c.download(data, dest)
        log["step"]["downloaded"] = dest
        print("step downloaded:", dest, Path(dest).stat().st_size, "bytes")

        # ---- 3) chain 续接该任务 ----
        r = requests.post(f"{BASE}/v1/task/chain", headers=hdr, timeout=30, json={
            "task_id": task_id, "theme_id": STEP_THEME, "theme_item_id": STEP_ITEM,
            "restricted_type": "FREE_USER", "mode_type": "GENERATE",
        })
        print("chain submit:", r.status_code, r.text[:200])
        log["chain"]["submit"] = {"status": r.status_code, "body": r.text[:300]}
        if r.status_code == 200:
            chain_id = r.json()["data"]["taskId"]
            cdata = c.poll(chain_id, timeout_s=420, interval_s=4)
            log["chain"]["task_id"] = chain_id
            log["chain"]["result"] = {k: v for k, v in cdata.items()
                                      if k in ("status", "failedReason", "s3Key", "usedPoint")}
            print("chain result:", log["chain"]["result"])

    OUT.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT)


if __name__ == "__main__":
    main()
