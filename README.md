# SnapClip 1.1.1 APK 逆向 → 2API 网关

对 `ai.video.photo.animator.face.dance.animation`（SnapClip 1.1.1，Flutter）的协议还原，
产出可运行的 2API 网关、中文 demo 页、本地压测脚本与 new-api 任务插件。

- **认证链已打通**：匿名 provider 免凭证签发 RS256 JWT（`X-ACCESS-TOKEN`），无签名、无证书固定、无设备强校验。
- **真实出片已验证**：单图 → 5.04 s 竖屏视频（784×1176 / 24 fps / H.264 + AAC / 约 4 MB）。
- **目录已全量抓取**：17 个主题分类、298 个模板条目、12 个生成/轮询端点。
- **额度机制已实测**：每个匿名 uid 每日 3 次免费，第 4 次返回 `400 / code 2100 Free user daily task quota used up`。

## 目录

```
snapclip_client.py         协议客户端（匿名 token / S3 直传 / 提交 / 轮询 / 下载）
server.py                  2API 网关（mock | real 双模，账号池，OpenAI 风格 + 原生路由）
demo.html                  中文 demo 页（选模板 → 上传图 → 生成 → 播放）
bench.py                   本地压测（只压自己的网关，mock 模式）
verify_demo.py             Playwright 真实浏览器验证
test_plugin.mjs            new-api 插件契约测试（9 项断言）
plugins/tasks/snapclip/    new-api 任务插件（渠道类型 74）
docs/REVERSE-REPORT.md     逆向报告（端点全表 / 认证链 / 计费 / 证据）
docs/NEW-API-INTEGRATION.md 对接指南（部署 / 字段配置 / 验证四步 / 排错表）
probe/                     探测原始响应（api_meta / 分类 / 模板 / 额度边界）
```

## 快速开始

```bash
pip install fastapi uvicorn requests boto3 aiohttp

# 1) 演示（不触碰上游，1.2 s 返回占位视频）
python server.py --port 8940 --mode mock
# 打开 http://127.0.0.1:8940/demo

# 2) 真实出片（消耗 1 次免费额度，约 60–120 s）
python snapclip_client.py D:/path/face.jpg
# 或经网关：curl -X POST http://127.0.0.1:8940/v1/videos/generations ...

# 3) 本地压测（压自己的网关）
python bench.py --base http://127.0.0.1:8940 --concurrency 20 --rounds 50

# 4) 浏览器验证
python verify_demo.py http://127.0.0.1:8940

# 5) 插件契约
node test_plugin.mjs http://127.0.0.1:8940
```

## 协议摘要（详见 docs/REVERSE-REPORT.md）

```http
# ① 匿名身份（无需任何前置凭证）
POST https://saas.castbox.fm/auth/api/v1/tokens/provider/secret
X-APP-ID: ai-video
{"provider":"anonymous","secret":"<任意随机串>"}
→ {"data":{"uid":"AV-YZPTF","token":"<RS256 JWT, exp≈10y>"}}

# ② 上传凭证（STS，count ∈ [1,20]）
GET https://ai-video.thebetter.ai/v1/token/generate_image_token?count=1
X-APP-ID: ai-video   X-ACCESS-TOKEN: <JWT>
→ imageS3KeyList + accessKeyId/secretAccessKey/sessionToken   # bucket cre8vid-user, us-east-1

# ③ 直传 S3  →  ④ 提交任务  →  ⑤ 轮询  →  ⑥ 下载成品
PUT  s3://cre8vid-user/<预置 key>
POST /v1/task/magic   {"s3_key_list":[key],"theme_id":"…","theme_item_id":"…","restricted_type":"FREE_USER","mode_type":"GENERATE"}
GET  /v1/task/task_result?task_id=…
GET  s3://cre8vid-user/<s3Key>
```

11 个生成端点同构：`emote / hug / magic / beastify / transform / video_swap / dance / harry_potter / figure / step / chain`，
另有零鉴权的 `GET /v1/api_meta` 直接自描述全部端点与参数。

## 验证记录（本轮实测）

| 验证 | 命令 | 结果 |
|---|---|---|
| 真实出片 | `python snapclip_client.py` | SUCCESS，77.2 s，4.21 MB，`ffprobe` → h264 784×1176 24fps 5.04s + aac |
| 额度边界 | `python probe/quota_probe.py` | 3 次成功（69.7 / 71.5 / 62.6 s，usedPoint=0），第 4 次 `400 code=2100` |
| 网关 real 冒烟 | `POST :8941/v1/videos/generations {"wait":true}` | SUCCESS，114.8 s，3.8 MB |
| demo 页 e2e（mock） | `python verify_demo.py :8940` | `state=SUCCESS`、`page_errors=[]`，截图 `demo_screenshot.png` |
| demo 页 e2e（real） | `python verify_demo.py :8941 face.jpg` | `state=SUCCESS`、耗时 103.8 s、`video_src` 就绪，截图 `demo_screenshot_real.png` |
| 插件契约 | `node test_plugin.mjs` | **9/9 PASS**（submit/query/poll/artifact/缺参拒绝/多图透传） |
| 成品抽帧 | `ffmpeg -ss 1.5 -frames:v 1` | `docs/sample_frame.png`（Pixi 风格化人像，确认非占位视频） |
| 本地压测 | `python bench.py -c 20 -n 50`（mock） | 见下表 |

> 修复记录：首版网关把「本地路径图片」当临时文件在任务结束时删除，导致用户素材 `D:/apk-analysis/photify/face.jpg`
> 被误删。现 `_decode_image` 返回 `(path, is_temp)`，仅清理下载/解码出的临时文件，本地原图不动（已用 real 模式回归验证原图保留）。

本地压测（Windows 单机、mock 模式、均为 200/404 预期码，零失败）：

| 场景 | n | rps | p50 | p95 | p99 |
|---|---|---|---|---|---|
| GET /health | 50 | 612.6 | 14.59 ms | 53.07 ms | 53.60 ms |
| GET /v1/models | 50 | 1090.6 | 16.23 ms | 18.97 ms | 19.87 ms |
| GET /api/v1/themes | 50 | 1343.8 | 12.81 ms | 16.69 ms | 17.64 ms |
| POST /v1/videos/generations (mock) | 50 | 1775.0 | 8.77 ms | 14.92 ms | 15.42 ms |
| GET task 404（错误路径） | 20 | 1413.8 | 11.22 ms | 12.12 ms | 12.12 ms |

> 真实上游单任务 60–120 s 属上游 GPU 排队，与网关自身吞吐无关；压测只反映网关开销。

### 第二轮：上游真实并发（`probe/pool_stress.py`，全部全新匿名账号、全程 `usedPoint=0`）

| 配置 | wall | 成功 | 单任务 p50 | 单任务 max | token / 上传 / 提交 / 排队+生成 / 下载 |
|---|---|---|---|---|---|
| 8 任务 · 并发 4 | 154.2 s | **8/8** | 71.9 s | 85.2 s | 3.74 / 8.03 / 3.54 / 43.9 / 13.96 s |
| 8 任务 · 并发 8 | 73.9 s | **8/8** | 69.8 s | 73.9 s | 3.10 / 5.92 / 2.57 / 45.1 / 11.16 s |

并发 4 → 8 时 wall 近乎线性下降，上游排队时长不变（43.9 → 45.1 s），未见并发限流与 429。
账号池可水平扩展：每个匿名 secret 就是一个独立账号（每日 3 次额度），网关 `--pool N` 自动轮换。

### 第二轮：能力边界定论

| 能力 | 结论 | 证据 |
|---|---|---|
| **PRO 档**（vidu/kling/alibaba/pixverse，40–600 点） | **匿名账号不可用**：`400 code=2003 your device is not support for pro`，与 `X-DEVICE-INFO` 无关（3 种设备头形态全部同样报错）；任意非 `FREE_USER` 的 `restricted_type` 都走 PRO 分支 → 需 Google Play 内购账号 | `probe/pro_chain_probe.json` |
| **step 双图** | 可用：2 张图 → 960×960 / 4.03 s / 1.12 MB（`s3Key` 带 `_mva` 后缀），免费额度按 1 次计；网关 `images` 数组多图上传已实测（143.4 s，`usedPoint=0`） | `step_out.mp4` |
| **chain 续接** | 现有目录不可用：298 个模板全为 `concatType=CONCAT_NO`；提交会在 2 s 内 `FAILED/UNKNOWN` **且白扣 1 次免费额度**；参数层缺 `s3_key_list` → `1104`，假 ID → `21010`，跨账号 task_id 不校验归属 | `probe/pro_chain_probe.json` |
| `mode_type` 枚举校验 | 免费分支不校验（乱填 `WHATEVER` 也 200） | 同上 |

## 已知边界

- PRO 档（vidu / kling / alibaba / pixverse，40–600 点）未验证，本仓库不触发付费路径。
- `chain` / `step` 端点语义未验证（当前目录 298 个模板均为 `CONCAT_NO`）。
- `X-DEVICE-INFO` 服务端未强校验（不带该头全部接口正常），如上游收紧需补设备 JSON。
- 人脸检测阈值、图片最小尺寸、内容审核策略未验证。

## 合规声明

本项目仅做协议互操作研究：不复制、不重打包、不分发 APK 本体，不包含任何他人账号凭证或付费内容。
上游为第三方生产服务，接入生产前请自行评估服务条款与合规风险，并控制调用频次。
