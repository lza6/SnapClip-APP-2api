"""真实上游并发压测：多匿名账号池 + 并发任务，测各阶段耗时与失败分布

用法: python probe/pool_stress.py --tasks 8 --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from snapclip_client import SnapClipClient  # noqa: E402

IMG = "D:/apk-analysis/photify/test_face.jpg"
THEME = "vid_viral_comic_chaos_260527"
ITEM = "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4"


def one_task(idx: int) -> dict:
    t0 = time.perf_counter()
    row: dict = {"idx": idx}
    try:
        # 新实例 = 随机 secret = 全新匿名账号（等价于账号池扩一个新号）
        c = SnapClipClient()
        row["token_ms"] = round((time.perf_counter() - t0) * 1000)
        row["account"] = c.token[-6:]
        t1 = time.perf_counter()
        bucket, key = c.upload_image(IMG)
        row["upload_ms"] = round((time.perf_counter() - t1) * 1000)
        t2 = time.perf_counter()
        task_id = c.submit("magic", [key], THEME, ITEM, bucket=bucket)
        row["submit_ms"] = round((time.perf_counter() - t2) * 1000)
        row["task_id"] = task_id
        t3 = time.perf_counter()
        data = c.poll(task_id, timeout_s=600, interval_s=3)
        row["queue_s"] = round(time.perf_counter() - t3, 1)
        row["status"] = data.get("status")
        row["used_point"] = data.get("usedPoint")
        if data.get("status") == "SUCCESS":
            t4 = time.perf_counter()
            dest = f"stress_{idx}.mp4"
            c.download(data, dest)
            row["download_ms"] = round((time.perf_counter() - t4) * 1000)
            row["bytes"] = Path(dest).stat().st_size
    except Exception as exc:  # noqa: BLE001
        row["status"] = "ERROR"
        row["error"] = str(exc)[:200]
    row["total_s"] = round(time.perf_counter() - t0, 1)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=8)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    sem = asyncio.Semaphore(args.concurrency)
    loop = asyncio.get_running_loop()

    async def worker(i: int) -> dict:
        async with sem:
            return await loop.run_in_executor(None, one_task, i)

    t0 = time.perf_counter()
    rows = await asyncio.gather(*[worker(i) for i in range(args.tasks)])
    wall = time.perf_counter() - t0

    ok = [r for r in rows if r.get("status") == "SUCCESS"]
    totals = [r["total_s"] for r in ok]
    summary = {
        "tasks": args.tasks,
        "concurrency": args.concurrency,
        "wall_s": round(wall, 1),
        "success": len(ok),
        "failed": len(rows) - len(ok),
        "status_dist": {s: sum(1 for r in rows if r.get("status") == s)
                        for s in {r.get("status") for r in rows}},
        "total_p50_s": round(statistics.median(totals), 1) if totals else None,
        "total_max_s": round(max(totals), 1) if totals else None,
        "token_ms_avg": round(statistics.fmean([r["token_ms"] for r in rows if "token_ms" in r])),
        "upload_ms_avg": round(statistics.fmean([r["upload_ms"] for r in ok])) if ok else None,
        "submit_ms_avg": round(statistics.fmean([r["submit_ms"] for r in ok])) if ok else None,
        "queue_s_avg": round(statistics.fmean([r["queue_s"] for r in ok]), 1) if ok else None,
        "download_ms_avg": round(statistics.fmean([r["download_ms"] for r in ok])) if ok else None,
        "used_point_nonzero": [r.get("used_point") for r in ok if r.get("used_point")],
    }
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=1))
    Path("probe/pool_stress_result.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
