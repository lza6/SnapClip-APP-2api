# SnapClip 1.1.1 逆向报告

> 目标 `SnapClip_1.1.1-25031263.apk`（66.0 MB）
> 分析时间 2026-10-29 · 全部结论均附实测证据，未验证项单独标注

## 1. 基本信息

| 项 | 值 |
|---|---|
| 包名 | `ai.video.photo.animator.face.dance.animation` |
| 版本 | 1.1.1 (versionCode 25031263) |
| 技术栈 | Flutter（`libapp.so` Dart AOT，11.6 MB）+ 广告 SDK（Pangle / AppLovin / ironSource / Amazon APS） |
| 生产域名 | `https://ai-video.thebetter.ai` |
| 测试域名 | `https://test.ai-video.thebetter.ai` |
| 账号/内购后端 | `https://saas.castbox.fm`（Spring Boot，Guru SaaS SDK） |
| Firebase 项目 | `ai-video-dbf04`（只用于 RemoteConfig/分析，**不是**业务登录入口） |
| CDN | `https://d1erst0b2qwg7a.cloudfront.net` |
| 对象存储 | S3 `us-east-1`，bucket `cre8vid-user` |
| APK 签名 SHA1 | `52:E0:67:8A:24:F8:B5:AC:09:AD:99:69:53:04:A2:AB:B6:B4:D6:AD` |

Dart 层符号（`package:ai_video/…`）暴露了完整目录结构：`data/network/{api_meta_api,task_api,other_api,dio_builder}.dart`、
`ui/dialog/preview/mixin_upload_image.dart`、`utils/{photo_task_manager,task_queue,meta_api_manager}.dart`。

## 2. 认证链（核心突破）

业务接口需要三个请求头：

```
X-APP-ID: ai-video
X-ACCESS-TOKEN: <RS256 JWT>
X-DEVICE-INFO: <设备 JSON>   # 可选，服务端不强制
```

`X-ACCESS-TOKEN` 由 Guru SaaS 的 **匿名 provider** 签发，且**无需任何前置凭证**：

```http
POST https://saas.castbox.fm/auth/api/v1/tokens/provider/secret
X-APP-ID: ai-video
Content-Type: application/json

{"provider":"anonymous","secret":"<任意随机串>"}
```

```json
{"data":{"uid":"AV-YZPTF","uidInt":484071,"token":"eyJ0eXAiOiJKV1QiLCJ…"}}
```

JWT 载荷（实测解出）：

```json
{"sub":"AV-YZPTZ","aud":"ai-video","provider":"uuid","iss":"ai-video",
 "exp":2108642291,"iat":1793282291}
```

- `secret` 是**身份主键**：同 secret 幂等返回同一 uid，不同 secret → 新 uid（实测 `same secret -> uid AV-YZPTZ / AV-YZPTZ`，`diff secret -> AV-YZPTE`）。
- `exp` 距今约 10 年，token 无需刷新即可长用。
- 错误信息引导：缺 `X-APP-ID` → `missing X-APP-ID`；app_id 猜错 → `app_id not match`；缺 token → `access token can't be null` / `access_token invalid`。

> 结论：**匿名身份可无限生成**，每个匿名 uid 独立享有每日免费额度（见 §6）。

## 3. 上传链路（S3 直传）

```http
GET /v1/token/generate_image_token?count=1        # count ∈ [1,20]
```

```json
{"data":{"imageS3KeyList":["202610/29/dda8d8….jpg"],"S3bucket":"cre8vid-user",
 "accessKeyId":"ASIA…","secretAccessKey":"…","sessionToken":"IQoJb3JpZ2luX2Vj…"}}
```

- 返回 AWS STS 临时凭证（有效期约 1 h）+ **服务端预生成的 object key**。
- 客户端直接 `PUT s3://cre8vid-user/<key>`（`s3.us-east-1.amazonaws.com`，`s3-accelerate.amazonaws.com` 也在二进制中）。
- 参数校验先于鉴权：`?Count=1`（大小写错）报 `invalid GetMultiUploadImageTokenRequest.Count: value must be inside range [1, 20]`。

## 4. 任务 API

`GET /v1/api_meta`（**零鉴权**）直接吐出自描述接口清单，12 个端点：

| apiId | method | endpoint | 说明 |
|---|---|---|---|
| emote_api | POST | `/v1/task/emote` | 表情包效果 |
| hug_api | POST | `/v1/task/hug` | 拥抱效果 |
| magic_api | POST | `/v1/task/magic` | 魔法效果 |
| beastify_api | POST | `/v1/task/beastify` | 动物化 |
| transform_api | POST | `/v1/task/transform` | 变形 |
| videoswap_api | POST | `/v1/task/video_swap` | 换脸 |
| dance_api | POST | `/v1/task/dance` | 舞蹈 |
| harrypotter_api | POST | `/v1/task/harry_potter` | 哈利波特 |
| figure_api | POST | `/v1/task/figure` | 手办化 |
| step_api | POST | `/v1/task/step` | 多步骤组合 |
| chain_api | POST | `/v1/task/chain` | 续接上一次任务（传 `task_id`） |
| polling_api | GET | `/v1/task/task_result?task_id=` | 轮询（建议 1–3 s） |

统一提交体（11 个生成端点同构）：

```json
{"s3_bucket":"cre8vid-user","s3_key_list":["202610/29/….jpg"],
 "theme_id":"vid_viral_comic_chaos_260527",
 "theme_item_id":"vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4",
 "restricted_type":"FREE_USER","mode_type":"GENERATE"}
```

响应：`{"code":0,"data":{"taskId":"01a187ef-5861-76a5-8a47-a7332de93196"}}`

轮询响应（成功）：

```json
{"status":"SUCCESS","s3Key":"202610/29/vidu_<taskId>.mp4","s3Bucket":"cre8vid-user",
 "accessKeyId":"…","secretAccessKey":"…","sessionToken":"…",
 "expirationTime":"2026-10-29T14:53:37Z","createdAt":"…","finishedAt":"…",
 "waitSeconds":120,"usedPoint":0}
```

成品用同一套 STS 凭证 `GET s3://cre8vid-user/<s3Key>` 下载。

### 其它端点

| 端点 | 用途 |
|---|---|
| `GET /v1/task/theme_category_list` | 17 个主题分类 |
| `GET /v1/task/theme_image_hot_list` / `theme_image_list` / `theme_image_list_by_category` | 模板列表（分页 `page`/`size`） |
| `GET /v1/task/theme_image_info` | 单个模板详情（`themeId`+`themeItemId`） |
| `GET /v1/task/theme_and_image_list` | 主题+模板联合列表 |
| `GET /v1/task/theme_list_by_category` / `theme_tip_list` | 分类主题 / 提示文案 |
| `GET /v1/search/theme_image` | 模板搜索 |
| `GET /v1/banner/list` | 首页 banner（零鉴权） |
| `GET /v1/user/daily_free_count` | 今日剩余免费次数 `{"count":3}` |
| `GET /v1/api_meta` | 接口自描述（零鉴权） |

## 5. 模板目录（实测全量）

17 个分类、**298 个模板条目**（`probe/all_items.json`）：

| categoryId | 名称 | | categoryId | 名称 |
|---|---|---|---|---|
| vid_ego_music_me_260616 | Music Me | | vid_viral_comic_chaos_260527 | Comic Chaos |
| vid_viral_viral_dances_260611 | Viral Dances | | vid_viral_pet_dance_g_260416 | Pet Dance |
| vid_ego_funny_face_g_260706 | Funny Face | | video_shift_g_250717 | Photos Flow |
| vid_echo_photo_revival_260529 | Photo Revival | | video_magic_g_241011 | Magic Box |
| vid_viral_stadium_cam_g_260512 | Stadium Cam | | vid_viral_face_dance_g_260226 | Face Dance |
| vid_gaze_heart_vibe_g_260227 | Heart Vibe | | vid_ego_noir_portrait_g_260320 | Noir Portrait |
| vid_viral_ai_songs_g_260527 | AI Songs | | video_pet_g_250703 | Pet Party |
| vid_ego_power_vibe_260525 | Power Vibe | | vid_viral_future_star_g_260526 | Future Stars |
| vid_viral_future_baby_g_260519 | Future Baby | | | |

模板统计：`apiId` 分布 `magic_api 226 / figure_api 70 / step_api 2`；`inputCount` 1 张图 296 个、2 张图 2 个；
全部 `restrictedType=FREE_USER`、`concatType=CONCAT_NO`（当前目录无 chain 模板，chain 端点仍需传入历史 `task_id`）。

`generateModeList` 里的付费档（全部 `PRO_USER`，按点计费）：
`GENERATE_VIDU`(249) / `GENERATE_PIXVERSE`(20) / `GENERATE_KLING`(17) / `GENERATE_ALIBABA`(12)，点价 40–600。

## 6. 计费与免费额度

- 免费档不在 `generateModeList` 中：提交时用 `restricted_type=FREE_USER` + `mode_type=GENERATE` 即走免费额度。
- 每个匿名 uid 每日 `daily_free_count = 3`；成功后 `usedPoint=0`（不扣点）。
- 免费档无 PRO 模型的清晰度/时长优势：成品实测 **784×1176, 24 fps, 5.04 s, H.264+AAC, 4.2 MB**。
- 违规/失败任务（例如非法 s3 key）→ `status=FAILED, failedReason=UNKNOWN`，`usedPoint=0`，不扣额度。

## 7. 实测证据

| 验证 | 命令/方式 | 结果 |
|---|---|---|
| 零鉴权端点 | `curl https://ai-video.thebetter.ai/v1/api_meta` | 200，12 端点自描述 |
| 匿名签发 | `POST /auth/api/v1/tokens/provider/secret` | 200，JWT 348 字节，aud=ai-video |
| 额度查询 | `GET /v1/user/daily_free_count` | `{"count":3}` |
| S3 上传 | boto3 + STS | 上传成功（`202610/29/…jpg`） |
| 端到端出片 | `python snapclip_client.py` | `status=SUCCESS`，77.2 s（含上传+排队+出片），4.2 MB mp4 |
| 成品校验 | `ffprobe out_sample.mp4` | h264 784×1176 24fps 5.04s + aac |
| 身份幂等 | 同/异 secret 对比 | 同 secret → 同 uid；异 secret → 新 uid |

> 上述真实调用总计 < 10 次，均为免费额度，未触发任何付费路径。

## 8. 未验证 / 边界

- `X-DEVICE-INFO` 的具体 JSON 结构与服务端强校验程度：**未验证**（不带该头全部业务接口正常）。
- PRO 档（vidu/kling/alibaba/pixverse）真实出片：**未验证**（需账户点数，本报告不触发付费）。
- `chain` / `step` 端点的任务链语义：**未验证**（目录内无 chain 模板可用）。
- 同一匿名 uid 的额度耗尽后的错误码：见 `probe/quota_probe.json`（探针实测记录）。
- 人脸检测/内容审核策略、图片最小尺寸限制：**未验证**。

## 9. 风险提示

上游为第三方生产服务，匿名身份虽可无限签发，但高频调用会触发风控或额度策略调整；
本仓库的 demo 网关默认 `--mode mock`，真实模式应控制并发与频次，仅用于协议验证与自用。
