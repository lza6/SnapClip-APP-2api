"""Playwright 真实浏览器验证 demo 页：选模板 -> 生成 -> 轮询 -> 播放源就绪

用法:
  python verify_demo.py http://127.0.0.1:8940                 # mock 模式
  python verify_demo.py http://127.0.0.1:8941 D:/path/face.jpg # real 模式（走 data-uri 上传）
"""
import asyncio
import base64
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8940"
IMAGE = sys.argv[2] if len(sys.argv) > 2 else ""


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 860})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(f"{BASE}/demo", wait_until="networkidle")
        await page.wait_for_function("() => document.querySelectorAll('#theme option').length > 0")
        await page.select_option("#theme", index=0)
        await page.select_option("#modeSel", "free")
        if IMAGE:
            with open(IMAGE, "rb") as fh:
                uri = "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
            await page.fill("#imgUrl", uri)
        else:
            await page.fill("#imgUrl", "https://example.com/face.jpg")
        await page.click("#go")
        await page.wait_for_function(
            "() => ['SUCCESS','FAILED'].includes(document.getElementById('kState').textContent)",
            timeout=220000,
        )
        state = await page.text_content("#kState")
        src = await page.get_attribute("#out", "src")
        status = await page.text_content("#status")
        link = await page.text_content("#link")
        if state == "SUCCESS":
            # 让播放器跳到第 1 秒，截图能体现真实画面而不是黑屏首帧
            await page.evaluate("() => { const v = document.getElementById('out'); v.currentTime = 1; }")
            await page.wait_for_timeout(1500)
        shot = "demo_screenshot_real.png" if IMAGE else "demo_screenshot.png"
        await page.screenshot(path=shot, full_page=True)
        print(f"state={state}")
        print(f"video_src={src}")
        print(f"status={status}")
        print(f"link={link}")
        print(f"page_errors={errors}")
        await browser.close()
        if state != "SUCCESS" or not src:
            raise SystemExit("demo verification FAILED")


asyncio.run(main())
