# SnapClip → new-api 对接指南

本仓库提供 **2API 网关**（`server.py`）与 **new-api 任务插件**（`plugins/tasks/snapclip/plugin.js`），
把 SnapClip 的图生视频能力接成 new-api 的渠道（渠道类型 **74**，当前未被占用）。

> 上游能力：298 个模板、17 个主题分类、单图 → 5 s 竖屏视频（784×1176 / 24fps / H.264+AAC）。

## 1. 部署 2API 网关

```bash
pip install fastapi uvicorn requests boto3 aiohttp
python server.py --port 8941 --mode real --pool 4     # 生产用 real
python server.py --port 8940 --mode mock              # 压测/演示用 mock（完全本地，不触上游）
```

- `--mode real`：匿名账号池 + S3 直传 + 任务轮询，返回本地可下载的 mp4。
- `--mode mock`：不访问上游，固定 1.2 s 返回占位 mp4，**压测只压这个模式**。
- 网关自带 `accounts.json` 账号池：每个匿名 secret 对应一个 uid，每日 3 次免费额度，
  额度耗尽后自动追加新 secret（`/api/v1/accounts` 可查池状态）。

健康检查：`curl http://127.0.0.1:8941/health` → `{"ok":true,"mode":"real",...}`

## 2. 落地插件

```bash
cp -r plugins/tasks/snapclip  /path/to/new-api/plugins/tasks/
```

1. 复制后重启 new-api，前端「渠道 → 新建」会出现 **SnapClip**（type 74）任务渠道。
2. 渠道配置：

| 字段 | 值 | 说明 |
|---|---|---|
| 类型 | SnapClip (74) | 任务插件渠道 |
| Base URL | `http://<网关地址>:8941` | **不要**填 `ai-video.thebetter.ai`，认证与 S3 由网关处理 |
| 密钥 | 任意非空字符串 | 插件不走 key 校验，占位即可（网关内已做账号池） |
| 模型 | `snapclip-magic` 等 10 个 | 对应上游 `magic/figure/dance/emote/hug/transform/beastify/video_swap/harry_potter/step` |
| 分组/倍率 | 按次计费 | `extractUsage` 返回 null，无 token 倍率 |

3. 请求示例（OpenAI Responses 风格，走 `/v1/responses`）：

```json
{
  "model": "snapclip-magic",
  "input": [{"role":"user","content":[
      {"type":"input_image","image_url":"https://your.cdn/face.jpg"},
      {"type":"input_text","text":"生成 Chibi 动漫化视频"}
  ]}],
  "theme_id": "vid_viral_comic_chaos_260527",
  "theme_item_id": "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4",
  "mode": "free"
}
```

或走插件原生路由：`POST /snapclip/v1/videos/image2video`，字段同上（`image` 支持 URL / data-uri / base64）。

4. 幂等与超时：上游单任务 60–120 s；new-api 任务表 `per_task` 拉取，
   插件 `buildQueryRequest` 默认 20 s 超时，网关侧轮询 3 s 间隔、420 s 上限。

## 3. 计费建议

| 项 | 建议 |
|---|---|
| 计费单位 | 按次（`GENERATE` 档免费额度 / PRO 档 40–600 点，见 `docs/REVERSE-REPORT.md` §6） |
| 定价 | 网关一次调用 ≈ 70–120 s GPU 成本，建议 0.02–0.05 美元/次等价额度 |
| 免费额度 | 上游每匿名 uid 每日 3 次；网关账号池自动轮换，**不要**把 3 次/天写成对外承诺 |
| 防超发 | 网关 real 模式 `_executor` 6 并发上限；new-api 侧渠道建议限流 1–2 QPS |

## 4. 验证四步

```bash
# 1) 网关自检
curl http://127.0.0.1:8941/health
# 2) 真实出片（约 100 s，消耗 1 次免费额度）
curl -X POST http://127.0.0.1:8941/v1/videos/generations -H 'Content-Type: application/json' \
  -d '{"model":"snapclip/magic","image":"D:/path/face.jpg","mode":"free","wait":true}'
# 3) 插件契约（8 项断言，跑在 mock 网关）
node test_plugin.mjs http://127.0.0.1:8940
# 4) new-api 侧：选 SnapClip 渠道 → 提交 image2video → 任务详情出现视频 artifact
```

## 5. 排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| `app_id can't be null` | 少了 `X-APP-ID` | 插件/网关必须带 `X-APP-ID: ai-video` |
| `app_id not match` | app_id 猜错 | 正确值固定为 `ai-video` |
| `access token can't be null` / `access_token invalid` | token 缺失/过期 | 网关重新走匿名签发（`snapclip_client.anonymous_token`） |
| `Free user daily task quota used up`（400 / code 2100） | 该 uid 当日 3 次用尽 | 账号池换号；`/api/v1/accounts` 查配额 |
| `ID为 "..." 的记录不存在`（404 / code 21010） | `theme_item_id` 不完整或不存在 | 用 `GET /api/v1/themes` 拿完整 `themeItemId`（含 `.mp4` 后缀全路径） |
| 任务 `FAILED` + `failedReason=UNKNOWN` | 上传的 s3 key 非法/图片不可用 | 用网关 `/v1/token/generate_image_token` 预置 key 上传，不要自造 key |
| 网关 502 / 空响应 | STS 过期（约 1 h） | 每次任务重新取上传 token，网关已按此实现 |
| 视频 404 | `work/` 目录被清理 | 网关落盘目录需持久化，或让客户端直接用上游返回的 STS 下载 |

## 6. 边界与风险

- 上游是第三方生产服务，匿名身份可无限签发，但高频调用会触发风控；生产接入前先做小流量灰度。
- PRO 档（vidu/kling/alibaba/pixverse）需账户点数，网关默认只用免费档，未验证付费路径。
- `X-DEVICE-INFO` 服务端未强校验，网关不带；若上游后续收紧，需要在 `_headers` 里补设备 JSON。
- 本方案不复制、不重打包 APK，仅复刻公开 HTTP 协议；上线前请自行评估合规与上游条款。
