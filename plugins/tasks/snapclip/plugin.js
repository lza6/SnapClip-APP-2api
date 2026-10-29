export const meta = {
  apiVersion: 1,
  key: "snapclip",
  name: "SnapClip",
  icon: "SC",
  description: {
    en: "SnapClip photo-to-video (APK reverse, proxied via local 2API gateway)",
    zh: "SnapClip 图生视频（APK 逆向，经本地 2API 网关转发）",
  },
  version: "1.0.0",
  author: { name: "lza6" },
  channelTypes: [74],
  models: [
    "snapclip-magic",
    "snapclip-figure",
    "snapclip-dance",
    "snapclip-emote",
    "snapclip-hug",
    "snapclip-transform",
    "snapclip-beastify",
    "snapclip-video-swap",
    "snapclip-harry-potter",
    "snapclip-step",
  ],
  fetchMode: "per_task",
  protocols: [{ name: "openai_responses", supports: ["sync"] }],
  routes: [
    {
      method: "POST",
      path: "/snapclip/v1/videos/image2video",
      type: "submit",
      action: "image_to_video",
      decode: "decodeSubmit",
      render: "taskCreated",
    },
    {
      method: "GET",
      path: "/snapclip/v1/videos/image2video/:task_id",
      type: "query",
      render: "taskStatus",
    },
  ],
};

// 上游是自建 2API 网关（snapclip-server），由它负责匿名账号签发、S3 直传与轮询。
const FALLBACK_BASE = "http://127.0.0.1:8941";
const DEFAULT_THEME = "vid_viral_comic_chaos_260527";
const DEFAULT_ITEM = "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4";

// 模型名 -> 上游 api 段（POST /v1/task/<api>）
const MODEL_API = {
  "snapclip-magic": "magic",
  "snapclip-figure": "figure",
  "snapclip-dance": "dance",
  "snapclip-emote": "emote",
  "snapclip-hug": "hug",
  "snapclip-transform": "transform",
  "snapclip-beastify": "beastify",
  "snapclip-video-swap": "video_swap",
  "snapclip-harry-potter": "harry_potter",
  "snapclip-step": "step",
};

function trimmed(v) {
  return String(v === undefined || v === null ? "" : v).trim();
}

function gatewayBase(ctx) {
  const root = trimmed(ctx && ctx.baseUrl) || FALLBACK_BASE;
  return root.replace(/\/+$/, "");
}

/** 从 openai_responses / 原生请求体里取第一张图（URL 或 data-uri 均可）。 */
function firstImage(req) {
  if (!req) return "";
  if (Array.isArray(req.images) && req.images.length) return trimmed(req.images[0]);
  if (typeof req.image === "string" && trimmed(req.image)) return trimmed(req.image);
  if (typeof req.input_reference === "string" && trimmed(req.input_reference)) return trimmed(req.input_reference);
  const input = Array.isArray(req.input) ? req.input : [];
  for (let i = 0; i < input.length; i++) {
    const parts = (input[i] && input[i].content) || [];
    for (let j = 0; j < parts.length; j++) {
      if (parts[j] && parts[j].type === "input_image") {
        const v = parts[j].image_url;
        return trimmed(typeof v === "object" && v ? v.url : v);
      }
    }
  }
  return "";
}

export function buildSubmitRequest(ctx) {
  const req = ctx.requestBody || {};
  const image = firstImage(req);
  if (!image) throw new Error("image is required (URL / data-uri / base64)");

  const model = trimmed(req.model) || "snapclip-magic";
  const api = MODEL_API[model] || "magic";
  const payload = {
    model: "snapclip/" + api,
    image: image,
    theme_id: trimmed(req.theme_id) || DEFAULT_THEME,
    theme_item_id: trimmed(req.theme_item_id) || DEFAULT_ITEM,
    mode: trimmed(req.mode) || "free",
  };
  return {
    url: gatewayBase(ctx) + "/v1/videos/generations",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 60000,
  };
}

export function parseSubmitResponse(ctx, resp) {
  const body = resp.body || {};
  if (resp.statusCode !== 200) {
    throw new Error("snapclip submit failed: status " + String(resp.statusCode) + " " + trimmed(body.error || body.detail));
  }
  const id = trimmed(body.id);
  if (!id) throw new Error("snapclip: no task id in submit response");
  return { taskId: id, taskData: { upstream_id: id, model: trimmed(ctx.requestBody && ctx.requestBody.model) } };
}

export function buildQueryRequest(ctx) {
  const data = (ctx && ctx.taskData) || {};
  const id = trimmed(data.upstream_id) || trimmed(ctx && ctx.taskId);
  if (!id) throw new Error("snapclip: missing upstream task id");
  return {
    url: gatewayBase(ctx) + "/v1/videos/generations/" + id,
    method: "GET",
    headers: {},
    timeoutMs: 20000,
  };
}

export function parseTaskResult(ctx, body) {
  const status = trimmed(body && body.status).toUpperCase();
  const url = trimmed(body && body.url);
  if (status === "SUCCESS") {
    const abs = url.indexOf("http") === 0 ? url : gatewayBase(ctx) + url;
    return { code: 0, taskId: ctx.taskId, status: "SUCCESS", reason: "", url: abs };
  }
  if (status === "FAILED") {
    return { code: 0, taskId: ctx.taskId, status: "FAILURE", reason: trimmed(body.error) || "snapclip task failed" };
  }
  return { code: 0, taskId: ctx.taskId, status: "IN_PROGRESS", reason: "" };
}

export function listArtifacts(task) {
  const data = (task && task.data) || {};
  const result = data.result || data;
  const url = trimmed(result.url || result.video_url);
  return url ? [{ type: "video", url: url }] : [];
}

export function extractUsage() {
  return null; // 网关按次调用，无 token 倍率
}

export function extractUsageOnCompletion() {
  return null;
}

export function buildContentRequest() {
  return null;
}

export const native = {
  decodeSubmit: function (ctx) {
    if (!ctx.body || ctx.body.kind !== "json") throw new Error("JSON body required");
    const body = ctx.body.value || {};
    const model = trimmed(body.model) || "snapclip-magic";
    const image = firstImage(body);
    if (!image) throw new Error("image is required");
    return {
      kind: "submit",
      model: model,
      requestBody: {
        model: model,
        image: image,
        theme_id: trimmed(body.theme_id),
        theme_item_id: trimmed(body.theme_item_id),
        mode: trimmed(body.mode) || "free",
      },
    };
  },
  taskCreated: function (ctx, task) {
    const result = task.data || {};
    const data = result.data || {};
    return Object.assign({}, result, {
      data: Object.assign({}, data, { task_id: task.task_id }),
    });
  },
  taskStatus: function (ctx, task) {
    const result = task.data || {};
    const data = result.data || {};
    return Object.assign({}, result, {
      data: Object.assign({}, data, { task_id: task.task_id }),
    });
  },
};

export const protocols = {
  openai_responses: {
    renderFinal: function (ctx) {
      const url =
        trimmed(ctx.taskResult && (ctx.taskResult.url || ctx.taskResult.remote_url)) ||
        trimmed(((ctx.data || {}).data || {}).video_url);
      if (!url) throw new Error("video url is unavailable");
      const safe = url.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
      return '<video controls src="' + safe + '"></video>';
    },
    decodeRequest: function (ctx) {
      if (!ctx.body || ctx.body.kind !== "json") throw new Error("JSON body required");
      const req = ctx.body.value || {};
      const model = trimmed(ctx.model) || "snapclip-magic";
      const image = firstImage(req);
      if (!image) throw new Error("image is required");
      return {
        kind: "submit",
        model: model,
        action: "image_to_video",
        requestBody: {
          model: model,
          image: image,
          theme_id: trimmed(req.theme_id),
          theme_item_id: trimmed(req.theme_item_id),
          mode: trimmed(req.mode) || "free",
        },
      };
    },
  },
};
