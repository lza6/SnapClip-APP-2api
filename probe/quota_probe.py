"""免费额度边界探针：同一匿名账号连续生成，观察每日免费次数与超额错误"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from snapclip_client import SnapClipClient

IMG = "D:/apk-analysis/photify/face.jpg"
THEME = "vid_viral_comic_chaos_260527"
ITEM = "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4"

c = SnapClipClient()
c.token = c.anonymous_token("quota-probe-fixed-secret-01")
log = []
for i in range(1, 6):
    before = None
    try:
        before = c.daily_free_count()
    except Exception as exc:  # noqa: BLE001
        print("count error:", exc)
    entry = {"round": i, "count_before": before}
    try:
        res = c.generate(IMG, THEME, ITEM, api_id="magic", dest=f"quota_{i}.mp4")
        entry.update(status=res.status, used=res.raw.get("usedPoint"), elapsed=round(res.elapsed, 1),
                     failed_reason=res.raw.get("failedReason"), bytes=len(res.video or b""))
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:300]
    try:
        entry["count_after"] = c.daily_free_count()
    except Exception as exc:  # noqa: BLE001
        entry["count_after_error"] = str(exc)[:200]
    print(json.dumps(entry, ensure_ascii=False))
    log.append(entry)
    if "error" in entry:
        break
    time.sleep(1)

with open("probe/quota_probe.json", "w", encoding="utf-8") as fh:
    json.dump(log, fh, ensure_ascii=False, indent=1)
print("saved probe/quota_probe.json")
