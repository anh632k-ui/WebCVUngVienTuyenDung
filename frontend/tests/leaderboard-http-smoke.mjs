import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import process from "node:process";

const root = process.cwd(), bin = path.join(root, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath, backendPort = 3270, appPort = 3271, origin = "http://localhost:" + appPort;
const jobId = "00000000-0000-4000-8000-000000000011", matchId = "00000000-0000-4000-8000-000000000033";
const now = "2026-01-01T00:00:00Z";
const user = { id: jobId, email: "hr@example.test", full_name: "HR", role: "HR", phone_number: null, is_active: true, created_at: now, updated_at: now };
const match = { id: matchId, job_id: jobId, resume_id: "00000000-0000-4000-8000-000000000022", generation: 1, resume_revision: 1, job_revision: 1, status: "COMPLETED", overall_score: 89.5, skill_score: 90, semantic_score: 88, experience_score: 90, algorithm_version: "hybrid-v1", embedding_model: null, embedding_preprocessing_version: null, error_message: null, created_at: now, updated_at: now, calculated_at: now };
const received = [];
function send(res, status, body) { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); }
const backend = createServer(async (req, res) => {
  const route = new URL(req.url, "http://localhost").pathname, auth = req.headers.authorization;
  received.push({ route, auth, url: req.url });
  if (route === "/api/v1/users/me") return auth === "Bearer hr-token"
    ? send(res, 200, { success: true, data: user })
    : auth === "Bearer candidate-token"
      ? send(res, 200, { success: true, data: { ...user, role: "CANDIDATE" } })
      : send(res, 401, { success: false, error: { code: "INVALID_ACCESS_TOKEN", message: "Invalid" } });
  if (auth !== "Bearer hr-token") return send(res, 403, { success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Forbidden" } });
  if (route === "/api/v1/jobs/" + jobId + "/leaderboard") return send(res, 200, { success: true,
    data: [{ rank: 1, match, candidate: { resume_id: match.resume_id, full_name: "Nguyễn A", current_title: "Developer" } }],
    meta: { page: 1, limit: 20, total_items: 1, total_pages: 1 } });
  return send(res, 404, { success: false, error: { code: "NOT_FOUND", message: "Not found" } });
});
const cookie = "cvinsight_session=hr-token";
const get = (route, init = {}) => fetch(origin + route, { redirect: "manual", ...init });
async function verify() {
  const url = "/api/hr/jobs/" + jobId + "/leaderboard";
  assert.equal((await get(url)).status, 401);
  assert.equal((await get(url, { headers: { cookie: "cvinsight_session=candidate-token" } })).status, 403);
  assert.equal((await get(url + "?min_score=101", { headers: { cookie } })).status, 422);
  const result = await get(url + "?page=1&limit=20&min_score=80", { headers: { cookie } });
  assert.equal(result.status, 200);
  assert.match(result.headers.get("cache-control") || "", /no-store/);
  const body = await result.json();
  assert.equal(body.data[0].rank, 1);
  assert.equal(body.data[0].match.overall_score, 89.5);
  assert.match(received.at(-1).url, /min_score=80/);
  const page = await get("/hr/jobs/" + jobId + "/leaderboard", { headers: { cookie } });
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /noindex, nofollow/);
  assert.doesNotMatch(html, /hr-token/);
  assert.equal((await get("/hr/jobs/" + jobId + "/leaderboard", { headers: { cookie: "cvinsight_session=candidate-token" } })).status, 307);
  const sitemap = await (await get("/sitemap.xml")).text();
  assert.doesNotMatch(sitemap, /leaderboard|\/hr\/jobs/);
}
async function ready() {
  for (let i = 0; i < 100; i++) { try { const res = await get("/"); if (res.ok) return; } catch { /* starting */ } await new Promise((resolve) => setTimeout(resolve, 250)); }
  throw new Error("Next server not ready");
}
function build() {
  assert.ok(npmCli);
  const p = spawnSync(process.execPath, [npmCli, "run", "build"], { cwd: root,
    env: { ...process.env, BACKEND_API_URL: "http://127.0.0.1:" + backendPort, SITE_URL: "", SEO_INDEXING_ENABLED: "false" }, stdio: "inherit" });
  assert.equal(p.status, 0);
}
let next;
try {
  await new Promise((resolve, reject) => { backend.once("error", reject); backend.listen(backendPort, "127.0.0.1", resolve); });
  build();
  next = spawn(process.execPath, [bin, "start", "-p", String(appPort)], { cwd: root,
    env: { ...process.env, BACKEND_API_URL: "http://127.0.0.1:" + backendPort, SITE_URL: "", SEO_INDEXING_ENABLED: "false" }, stdio: "ignore" });
  await ready(); await verify(); console.log("Production leaderboard HTTP smoke PASS");
} finally {
  if (next) { next.kill(); await Promise.race([new Promise((resolve) => next.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 3000))]); }
  await new Promise((resolve) => backend.close(resolve));
}
