// 插件契约冒烟：用真实 HTTP 打本地 mock 网关，验证 submit/query 全链路
import { buildSubmitRequest, parseSubmitResponse, buildQueryRequest, parseTaskResult, listArtifacts, meta } from "./plugins/tasks/snapclip/plugin.js";

const BASE = process.argv[2] || "http://127.0.0.1:8940";
const ctx = {
  baseUrl: BASE,
  requestBody: {
    model: "snapclip-magic",
    image: "https://example.com/face.jpg",
    theme_id: "vid_viral_comic_chaos_260527",
    theme_item_id: "vid_viral_comic_chaos_260527/0015_comic_chibi_me_260707.mp4",
    mode: "free",
  },
};

let ok = 0;
let fail = 0;
const check = (name, cond, extra = "") => {
  if (cond) { ok++; console.log("PASS", name, extra); } else { fail++; console.log("FAIL", name, extra); }
};

check("meta.channelTypes=74", meta.channelTypes.includes(74));

const req = buildSubmitRequest(ctx);
check("submit url", req.url === BASE + "/v1/videos/generations", req.url);
check("submit body has model+image", JSON.parse(req.body).model === "snapclip/magic" && JSON.parse(req.body).image.includes("face.jpg"));

const resp = await fetch(req.url, { method: req.method, headers: req.headers, body: req.body });
const body = await resp.json();
const parsed = parseSubmitResponse(ctx, { statusCode: resp.status, body });
check("submit -> taskId", typeof parsed.taskId === "string" && parsed.taskId.startsWith("vid_"), parsed.taskId);

const q = buildQueryRequest({ baseUrl: BASE, taskId: parsed.taskId, taskData: parsed.taskData });
check("query url", q.url === BASE + "/v1/videos/generations/" + parsed.taskId, q.url);

let result = { status: "IN_PROGRESS" };
for (let i = 0; i < 30; i++) {
  const qr = await fetch(q.url, { method: q.method, headers: q.headers });
  const qb = await qr.json();
  result = parseTaskResult({ baseUrl: BASE, taskId: parsed.taskId }, qb);
  if (result.status === "SUCCESS" || result.status === "FAILURE") break;
  await new Promise((r) => setTimeout(r, 1000));
}
check("poll -> SUCCESS", result.status === "SUCCESS", JSON.stringify(result));

const arts = listArtifacts({ data: { result } });
check("artifact url absolute", arts.length === 1 && arts[0].url.startsWith("http"), JSON.stringify(arts));
check("missing image rejected", (() => { try { buildSubmitRequest({ baseUrl: BASE, requestBody: {} }); return false; } catch { return true; } })());

// 多图（step 类模板）: images 数组应透传为 images 字段
const multi = buildSubmitRequest({
  baseUrl: BASE,
  requestBody: { model: "snapclip-step", image: "a.jpg", images: ["a.jpg", "b.jpg"],
                 theme_id: "video_shift_g_250717",
                 theme_item_id: "video_shift_g_250717/0006_shift_youth_250717.mp4" },
});
const mbody = JSON.parse(multi.body);
check("multi-image passthrough", Array.isArray(mbody.images) && mbody.images.length === 2 && mbody.model === "snapclip/step", JSON.stringify(mbody.images));

console.log(`SUMMARY ok=${ok} fail=${fail}`);
process.exit(fail ? 1 : 0);
