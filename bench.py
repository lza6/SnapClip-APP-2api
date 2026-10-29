"""本地压测：只压自己的网关（mock 模式），不触碰上游。

用法:
  python server.py --port 8940 --mode mock &
  python bench.py --base http://127.0.0.1:8940 --concurrency 20 --rounds 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import aiohttp

DEFAULT_BODY = {
    "model": "snapclip/magic",
    "image": "",
    "theme_id": "vid_viral_comic_chaos_260527",
    "theme_item_id": "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4",
    "mode": "free",
}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100 * len(ordered) + 0.5)) - 1))
    return ordered[k]


async def timed(session: aiohttp.ClientSession, method: str, url: str, **kw) -> tuple[float, int]:
    t0 = time.perf_counter()
    async with session.request(method, url, **kw) as resp:
        await resp.read()
        return (time.perf_counter() - t0) * 1000, resp.status


async def scenario(session: aiohttp.ClientSession, name: str, method: str, path: str,
                   concurrency: int, rounds: int, body: dict | None = None) -> dict:
    lat: list[float] = []
    codes: dict[int, int] = {}
    sem = asyncio.Semaphore(concurrency)

    async def one() -> None:
        async with sem:
            kw = {"json": body} if body is not None else {}
            ms, code = await timed(session, method, path, **kw)
            lat.append(ms)
            codes[code] = codes.get(code, 0) + 1

    t0 = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(rounds)])
    wall = time.perf_counter() - t0
    return {
        "scenario": name,
        "n": len(lat),
        "ok_codes": codes,
        "rps": round(len(lat) / wall, 1),
        "p50_ms": round(pct(lat, 50), 2),
        "p95_ms": round(pct(lat, 95), 2),
        "p99_ms": round(pct(lat, 99), 2),
        "max_ms": round(max(lat), 2),
        "mean_ms": round(statistics.fmean(lat), 2),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8940")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=30)
    args = ap.parse_args()

    results = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        results.append(await scenario(session, "GET /health", "GET", f"{args.base}/health", args.concurrency, args.rounds))
        results.append(await scenario(session, "GET /v1/models", "GET", f"{args.base}/v1/models", args.concurrency, args.rounds))
        results.append(await scenario(session, "GET /api/v1/themes", "GET", f"{args.base}/api/v1/themes", args.concurrency, args.rounds))
        results.append(await scenario(session, "POST /v1/videos/generations (mock)",
                                      "POST", f"{args.base}/v1/videos/generations", args.concurrency, args.rounds, DEFAULT_BODY))
        # 错误路径：缺 image（real 模式才校验，mock 直接成功）与未知任务
        results.append(await scenario(session, "GET task 404", "GET",
                                      f"{args.base}/v1/videos/generations/nope", args.concurrency, min(args.rounds, 20)))
        stats = await (await session.get(f"{args.base}/stats")).json()

    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    print("stats:", json.dumps(stats, ensure_ascii=False))
    with open("benchmark_results.json", "w", encoding="utf-8") as fh:
        json.dump({"base": args.base, "concurrency": args.concurrency, "rounds": args.rounds,
                   "results": results, "stats": stats}, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
