# SnapClip → new-api 交接执行单（HANDOFF）

> **读者**：接手 new-api 落地的人 / 另一个 Agent 会话。
> **目标**：把 SnapClip 图生视频接成 new-api 的一个任务渠道（渠道类型 **74**），端到端可出片。
> **不要重做逆向**：协议已还原并实测，本单据可直接执行；遇到"为什么这样"请看 `docs/REVERSE-REPORT.md`。

---

## 0. 前置检查（3 条，任一不满足先解决）

```bash
python -c "import fastapi,uvicorn,requests,boto3,aiohttp; print('deps ok')"
curl -s -m 5 http://127.0.0.1:8941/health      # 期望 {"ok":true,"mode":"real",...}
cd <new-api 仓库> && go build ./... >/dev/null && echo "new-api build ok"
```

若网关没在跑：

```bash
cd <本仓库>
pip install fastapi uvicorn requests boto3 aiohttp
python server.py --port 8941 --mode real --pool 4
```

`--mode real` 才真正出片；`--mode mock` 只用于压测网关本身。`accounts.json` 是账号池（自动生成，勿提交）。

---

## 1. 复制插件

```bash
cp -r <本仓库>/plugins/tasks/snapclip  <new-api 仓库>/plugins/tasks/
```

插件契约可用下面命令自检（在网关已启动时）：

```bash
node <本仓库>/test_plugin.mjs http://127.0.0.1:8941     # 期望 SUMMARY ok=9 fail=0
```

---

## 2. 注册渠道类型 74（3 处 Go 改动，必须全做）

### 2.1 `constant/channel.go` —— 常量

在 `ChannelTypeDuck = 73` 之后、`ChannelTypeDummy` 之前加一行：

```go
	ChannelTypeSnapClip = 74
```

### 2.2 `constant/channel.go` —— 显示名（`ChannelTypeNames` map，约 227 行）

```go
	ChannelTypeSnapClip: "SnapClip",
```

### 2.3 `constant/channel.go` —— `ChannelBaseURLs` 数组

数组按类型号下标取值，必须在末尾（`ChannelTypeDuck` 那一项之后）追加一项，否则下标错位：

```go
	"",                                    // 74 SnapClip（网关地址由渠道配置填写）
```

### 2.4 `relay/relay_adaptor.go` —— 任务平台映射（`taskPluginKeys`，约 191 行）

```go
	constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeSnapClip)): "snapclip",
```

> 这行是插件能否被路由到的关键：`snapclip` 必须与 `plugins/tasks/snapclip/plugin.js`
> 里的 `meta.key = "snapclip"` 完全一致。

### 2.5 编译与自检

```bash
gofmt -w constant/channel.go relay/relay_adaptor.go
go build ./... && echo "build ok"
cd relaykit && GOWORK=off go build ./... && cd ..    # relaykit 必须独立可构建
```

> 本改动不触碰数据库 schema、不涉及 migration，无需三库验证；但若你还改了别的持久化代码，
> 按 AGENTS.md 的数据库矩阵要求执行。

---

## 3. 建渠道（控制台字段）

| 字段 | 值 | 备注 |
|---|---|---|
| 类型 | **SnapClip (74)** | 新建后应出现在类型下拉 |
| 名称 | `SnapClip` | 任意 |
| Base URL | `http://<网关内网地址>:8941` | **不要**填 `ai-video.thebetter.ai`：认证/S3 由网关负责 |
| 密钥 | 任意非空（如 `snapclip-local`） | 插件不做 key 校验，账号池在网关侧 |
| 模型 | `snapclip-magic`、`snapclip-figure`、`snapclip-dance`、`snapclip-emote`、`snapclip-hug`、`snapclip-transform`、`snapclip-beastify`、`snapclip-video-swap`、`snapclip-harry-potter`、`snapclip-step` | 见插件 `meta.models` |
| 分组 | 按需 | 建议单开一个分组便于限流 |
| 限流 | 单渠道 1–2 QPS 起 | 上游单任务 40–120 s，别用 QPS 当并发 |

---

## 4. 端到端验收（4 步，全部要看真实输出）

```bash
# ① 网关直连出片（约 60–120 s，消耗该匿名账号 1 次免费额度）
curl -s -m 300 -X POST http://127.0.0.1:8941/v1/videos/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"snapclip/magic","image":"https://<你的图>/face.jpg","mode":"free","wait":true}'
# 期望：{"status":"SUCCESS","url":"/api/v1/files/....mp4","used_point":0,...}

# ② 多图（step）冒烟
curl -s -m 400 -X POST http://127.0.0.1:8941/v1/videos/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"snapclip/step","images":["<图1>","<图2>"],
       "theme_id":"video_shift_g_250717",
       "theme_item_id":"video_shift_g_250717/0006_shift_youth_250717.mp4","mode":"free","wait":true}'

# ③ new-api 侧提交（走插件原生路由）
curl -s -m 60 -X POST http://<new-api>/snapclip/v1/videos/image2video \
  -H 'Authorization: Bearer <你的 new-api token>' -H 'Content-Type: application/json' \
  -d '{"model":"snapclip-magic","image":"https://<你的图>/face.jpg","mode":"free"}'
# 期望：返回 task_id / 任务详情

# ④ 轮询到 SUCCESS，任务详情里出现 video artifact，且能播放（时长约 5 s、竖屏 784×1176）
curl -s http://<new-api>/snapclip/v1/videos/image2video/<task_id> -H 'Authorization: Bearer <token>'
```

四条都拿到成功结果，才算接完；只到 ③ 不算。

---

## 5. 计费配置建议

- 按次计费（`extractUsage` 返回 null，无 token 倍率）。
- 单次上游成本 ≈ 40–120 s GPU，建议对外价 0.02–0.05 元等价额度/次（按你的成本模型定）。
- **不要**把"每日 3 次免费"写进对外承诺：那是上游对每个匿名账号的限制，网关靠账号池轮换。
- 若做免费试用，用 new-api 的额度/分组控制，不要让用户直接打网关。

---

## 6. 排错表（按上游真实错误码）

| 现象 | 含义 | 处理 |
|---|---|---|
| `400 code=2003 your device is not support for pro` | 匿名账号无 PRO 权限 | **不要**给用户暴露 PRO 档；只能 `mode=free` |
| `400 code=2100 Free user daily task quota used up` | 该账号当日 3 次用尽 | 网关换号（`--pool` 调大）；查 `GET /api/v1/accounts` |
| `404 code=21010 ID为 "…" 的记录不存在` | `theme_item_id` 不完整 | 用 `GET /api/v1/themes` 取全路径（含 `.mp4`/`.mp3` 后缀） |
| `400 code=1200 theme item invalid` | step 模板只传了 1 张图 | step 必须 `images` 传 2 张 |
| `400 code=1104 User's input images count not match…` | chain 缺 `s3_key_list` | 别用 chain（见 §7） |
| 任务 `FAILED / failedReason=UNKNOWN` | 上传 key 非法 / 图片不可用 / chain 不支持 | 确认走网关 `/v1/token/generate_image_token` 预置 key；chain 直接放弃 |
| 网关 502 / 空响应 | STS 过期（约 1 h） | 每个任务重新取上传 token（网关已实现） |
| 视频 404 | `work/` 目录被清理 | 持久化 `work/`，或让客户端用上游 STS 直接下载 |
| `missing X-APP-ID` / `app_id not match` | 头写错 | 固定 `X-APP-ID: ai-video`（网关内部处理，渠道侧无需关心） |

---

## 7. 不要接的能力（已实测，接了就是坑）

| 能力 | 状态 | 证据 |
|---|---|---|
| PRO 档（vidu / kling / alibaba / pixverse，40–600 点） | **不可用**，匿名账号一律 2003，与设备头无关 | `docs/REVERSE-REPORT.md` §6.1 |
| `chain` 续接 | **不可用**：298 个模板全 `CONCAT_NO`，提交 2 s 内 FAILED **且白扣 1 次免费额度** | §6.3 |
| `video_swap` / `harry_potter` / `step` 之外的长尾端点 | 已在 `meta.models` 提供，但只有 `step`（双图）与 `magic/figure` 系实测过 | §6.2 |

---

## 8. 回滚

1. 控制台停用/删除 SnapClip 渠道（用户侧立即不可用）。
2. 移除 `plugins/tasks/snapclip/`。
3. 回退 §2 的 4 处 Go 改动（或 `git checkout constant/channel.go relay/relay_adaptor.go`），`go build ./...`。
4. 渠道类型停用前已产生的任务记录保留在任务表，不影响其它渠道。

> 注意：`ChannelBaseURLs` 是按类型号下标的数组，**删常量时必须同步删数组项**，否则所有渠道 base url 错位。

---

## 9. 变更影响面（给审查者）

- 改动文件：`constant/channel.go`（常量+名称+base url）、`relay/relay_adaptor.go`（平台映射）、新增 `plugins/tasks/snapclip/plugin.js`。
- 不改数据库、不改鉴权、不改既有渠道行为；新增类型号 74 未被占用（69=AIVG、70=OpenChat142、71=Zyra、72=AIVM、73=Duck）。
- 回归重点：其它任务渠道（aivg/kling/vidu/…）仍能提交与轮询；`go build ./...` 与 `test_plugin.mjs` 通过。
