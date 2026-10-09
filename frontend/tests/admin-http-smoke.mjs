import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const nextBin = path.join(root, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath;
const backendPort = 3280, appPort = 3281, origin = "http://localhost:" + appPort;
const adminId = "00000000-0000-4000-8000-000000000001";
const candidateId = "00000000-0000-4000-8000-000000000002";
const hrId = "00000000-0000-4000-8000-000000000003";
const now = "2026-01-01T00:00:00Z";
const admin = { id: adminId, email: "admin@example.test", full_name: "Admin Tester", phone_number: null, role: "ADMIN", is_active: true, created_at: now, updated_at: now };
const candidate = { ...admin, id: candidateId, email: "candidate@example.test", full_name: "Candidate", role: "CANDIDATE" };
const hr = { ...admin, id: hrId, email: "hr@example.test", full_name: "HR", role: "HR" };
const received = [];
function json(res, status, body) { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); }
async function requestBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const bytes = Buffer.concat(chunks).toString("utf8");
  return bytes ? JSON.parse(bytes) : null;
}
const backend = createServer(async (req, res) => {
  const body = await requestBody(req);
  const pathname = new URL(req.url, "http://localhost").pathname;
  const auth = req.headers.authorization;
  received.push({ pathname, url: req.url, method: req.method, auth, body });
  if (req.method === "GET" && pathname === "/api/v1/users/me") {
    if (auth === "Bearer admin-token") return json(res, 200, { success: true, data: admin });
    if (auth === "Bearer candidate-token") return json(res, 200, { success: true, data: candidate });
    return json(res, 401, { success: false, error: { code: "INVALID_ACCESS_TOKEN", message: "Invalid token" } });
  }
  if (auth !== "Bearer admin-token") return json(res, 403, { success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Forbidden" } });
  if (req.method === "GET" && pathname === "/api/v1/admin/users") {
    return json(res, 200, { success: true, data: [candidate, hr], meta: { page: 1, limit: 20, total_items: 2, total_pages: 1 } });
  }
  if (req.method === "PATCH" && pathname === "/api/v1/admin/users/" + candidateId) {
    if (body.role === "HR" && body.is_active === false) {
      return json(res, 409, { success: false, error: { code: "ROLE_CHANGE_BLOCKED", message: "Cannot switch role with owned resources" } });
    }
    return json(res, 200, { success: true, data: { ...candidate, ...body } });
  }
  return json(res, 404, { success: false, error: { code: "NOT_FOUND", message: "Not found" } });
});
const cookie = "cvinsight_session=admin-token", candidateCookie = "cvinsight_session=candidate-token";
const get = (route, init = {}) => fetch(origin + route, { redirect: "manual", ...init });
const mutationHeaders = { cookie, origin, "content-type": "application/json", "sec-fetch-site": "same-origin" };
async function verify() {
  const endpoint = "/api/admin/users";
  assert.equal((await get(endpoint)).status, 401);
  assert.equal((await get(endpoint, { headers: { cookie: candidateCookie } })).status, 403);
  const list = await get(endpoint + "?page=1&limit=20&role=CANDIDATE&is_active=true", { headers: { cookie } });
  assert.equal(list.status, 200);
  assert.match(list.headers.get("cache-control") || "", /no-store/);
  assert.equal((await list.json()).data[0].role, "CANDIDATE");
  assert.match(received.at(-1).url, /role=CANDIDATE/);
  assert.equal((await get(endpoint + "?role=SUPERUSER", { headers: { cookie } })).status, 422);
  assert.equal((await get(endpoint + "?limit=101", { headers: { cookie } })).status, 422);

  const patchUrl = endpoint + "/" + candidateId;
  assert.equal((await get(endpoint + "/not-uuid", { method: "PATCH", headers: mutationHeaders, body: JSON.stringify({ role: "HR" }) })).status, 422);
  const patched = await get(patchUrl, { method: "PATCH", headers: mutationHeaders, body: JSON.stringify({ is_active: false }) });
  assert.equal(patched.status, 200);
  assert.equal((await patched.json()).data.is_active, false);
  const count = received.filter((x) => x.method === "PATCH").length;
  assert.equal((await get(patchUrl, { method: "PATCH", headers: mutationHeaders, body: JSON.stringify({ role: "ADMIN" }) })).status, 422);
  assert.equal((await get(patchUrl, { method: "PATCH", headers: mutationHeaders, body: JSON.stringify({ email: "attacker@example.test" }) })).status, 422);
  assert.equal(received.filter((x) => x.method === "PATCH").length, count);
  assert.equal((await get(patchUrl, { method: "PATCH", headers: { ...mutationHeaders, origin: "https://evil.example" }, body: JSON.stringify({ role: "HR" }) })).status, 403);
  const conflict = await get(patchUrl, { method: "PATCH", headers: mutationHeaders, body: JSON.stringify({ role: "HR", is_active: false }) });
  assert.equal(conflict.status, 409);

  const page = await get("/admin/users", { headers: { cookie } });
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /noindex, nofollow/);
  assert.doesNotMatch(html, /admin-token/);
  assert.equal((await get("/admin/users", { headers: { cookie: candidateCookie } })).status, 307);
  const sitemap = await (await get("/sitemap.xml")).text();
  assert.doesNotMatch(sitemap, /admin\/users|\/api\/admin/i);
}
async function ready() {
  for (let attempt = 0; attempt < 100; attempt++) {
    try { const res = await get("/"); if (res.ok) return; } catch { /* Next is starting */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Next.js production server not ready");
}
function build() {
  assert.ok(npmCli, "npm_execpath must exist");
  const result = spawnSync(process.execPath, [npmCli, "run", "build"], {
    cwd: root, env: { ...process.env, BACKEND_API_URL: "http://127.0.0.1:" + backendPort, SITE_URL: "", SEO_INDEXING_ENABLED: "false" }, stdio: "inherit",
  });
  assert.equal(result.status, 0, "Next.js production build must pass");
}
let next;
try {
  await new Promise((resolve, reject) => { backend.once("error", reject); backend.listen(backendPort, "127.0.0.1", resolve); });
  build();
  next = spawn(process.execPath, [nextBin, "start", "-p", String(appPort)], {
    cwd: root, env: { ...process.env, BACKEND_API_URL: "http://127.0.0.1:" + backendPort, SITE_URL: "", SEO_INDEXING_ENABLED: "false" }, stdio: "ignore",
  });
  await ready();
  await verify();
  console.log("Production Admin HTTP smoke PASS");
} finally {
  if (next) {
    next.kill();
    await Promise.race([new Promise((resolve) => next.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 3000))]);
  }
  await new Promise((resolve) => backend.close(resolve));
}
