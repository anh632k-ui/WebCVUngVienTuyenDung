import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const nextBin = path.join(root, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath;
const backendPort = 3260, appPort = 3261, origin = "http://localhost:" + appPort;
const jobId = "00000000-0000-4000-8000-000000000011";
const cvId = "00000000-0000-4000-8000-000000000022";
const matchId = "00000000-0000-4000-8000-000000000033";
const now = "2026-01-01T00:00:00Z";
const user = { id: cvId, email: "candidate@example.test", full_name: "Candidate", role: "CANDIDATE", phone_number: null, is_active: true, created_at: now, updated_at: now };
const job = { id: jobId, recruiter_id: user.id, revision: 1, title: "Backend", job_level: "Junior", location: null, raw_content: "Python", min_experience_years: 1, education_requirement: null, parsing_status: "PARSED", is_criteria_verified: true, w_skill: .5, w_semantic: .3, w_experience: .2, status: "ACTIVE", created_at: now, updated_at: now, parsed_at: now };
const cv = { id: cvId, revision: 1, file_name: "cv.pdf", file_size: 800, mime_type: "application/pdf", parsing_status: "PARSED", is_manually_edited: false, created_at: now, parsed_at: now };
const match = { id: matchId, job_id: jobId, resume_id: cvId, generation: 1, resume_revision: 1, job_revision: 1, status: "COMPLETED", overall_score: 86, skill_score: 92, semantic_score: 84, experience_score: 76, algorithm_version: "hybrid-v1", embedding_model: "BAAI/bge-m3", embedding_preprocessing_version: "v1", error_message: null, created_at: now, updated_at: now, calculated_at: now, matched_skills: [{ skill_name: "Python" }], missing_skills: [{ skill_name: "Docker" }], gap_analysis_summary: "Thiếu Docker" };
const received = [];
function send(res, status, body) { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); }
async function bodyOf(req) { const chunks = []; for await (const c of req) chunks.push(c); const text = Buffer.concat(chunks).toString("utf8"); return text ? JSON.parse(text) : null; }
const backend = createServer(async (req, res) => {
  const body = await bodyOf(req), route = new URL(req.url, "http://localhost").pathname, auth = req.headers.authorization;
  received.push({ route, method: req.method, body, auth });
  if (route === "/api/v1/users/me") {
    return auth === "Bearer candidate-token" ? send(res, 200, { success: true, data: user })
      : auth === "Bearer hr-token" ? send(res, 200, { success: true, data: { ...user, role: "HR" } })
      : auth === "Bearer admin-token" ? send(res, 200, { success: true, data: { ...user, role: "ADMIN" } })
      : send(res, 401, { success: false, error: { code: "INVALID_ACCESS_TOKEN", message: "Invalid" } });
  }
  if (!["Bearer candidate-token", "Bearer hr-token"].includes(auth)) return send(res, 403, { success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Forbidden" } });
  if (route === "/api/v1/jobs" && req.method === "GET") return send(res, 200, { success: true, data: [job], meta: { page: 1, limit: 100, total_items: 1, total_pages: 1 } });
  if (route === "/api/v1/resumes" && req.method === "GET") return send(res, 200, { success: true, data: [cv], meta: { page: 1, limit: 100, total_items: 1, total_pages: 1 } });
  if (route === "/api/v1/matching" && req.method === "GET") return send(res, 200, { success: true, data: [match], meta: { page: 1, limit: 20, total_items: 1, total_pages: 1 } });
  if (route === "/api/v1/matching/calculate" && req.method === "POST") return send(res, 202, { success: true, data: { job_id: jobId, match_ids: [matchId], total_matches: 1, status: "PENDING" } });
  if (route === "/api/v1/matching/" + matchId && req.method === "GET") return send(res, 200, { success: true, data: match });
  if (route === "/api/v1/matching/" + matchId + "/gap-analysis" && req.method === "GET") return send(res, 200, { success: true, data: { match_id: matchId, overall_score: 86, skill_score: 92, semantic_score: 84, experience_score: 76, matched_skills: match.matched_skills, missing_skills: match.missing_skills, recommendation: "Thiếu Docker", explanation: null } });
  return send(res, 404, { success: false, error: { code: "NOT_FOUND", message: "Not found" } });
});
const candidate = "cvinsight_session=candidate-token", hr = "cvinsight_session=hr-token";
const headers = { cookie: candidate, origin, "content-type": "application/json", "sec-fetch-site": "same-origin" };
const get = (route, init = {}) => fetch(origin + route, { redirect: "manual", ...init });
async function verify() {
  assert.equal((await get("/api/workspace/matching")).status, 401);
  assert.equal((await get("/api/workspace/matching", { headers: { cookie: "cvinsight_session=admin-token" } })).status, 403);
  const jobs = await get("/api/workspace/matching/jobs?page=1&limit=100", { headers: { cookie: candidate } });
  assert.equal(jobs.status, 200);
  const resumes = await get("/api/workspace/matching/resumes?page=1&limit=100", { headers: { cookie: candidate } });
  assert.equal(resumes.status, 200);
  const list = await get("/api/workspace/matching", { headers: { cookie: candidate } });
  assert.equal(list.status, 200); assert.equal((await list.json()).data[0].overall_score, 86);
  assert.equal((await get("/api/workspace/matching?job_id=not-uuid", { headers: { cookie: candidate } })).status, 422);
  const payload = { job_id: jobId, resume_ids: [cvId] };
  const trigger = await get("/api/workspace/matching/calculate", { method: "POST", headers, body: JSON.stringify(payload) });
  assert.equal(trigger.status, 202, await trigger.clone().text()); assert.equal((await trigger.json()).data.status, "PENDING");
  const before = received.filter((e) => e.route === "/api/v1/matching/calculate").length;
  const invalid = await get("/api/workspace/matching/calculate", { method: "POST", headers, body: JSON.stringify({ ...payload, resume_ids: [cvId, cvId] }) });
  assert.equal(invalid.status, 422); assert.equal(received.filter((e) => e.route === "/api/v1/matching/calculate").length, before);
  const cross = await get("/api/workspace/matching/calculate", { method: "POST", headers: { ...headers, origin: "https://evil.test" }, body: JSON.stringify(payload) });
  assert.equal(cross.status, 403);
  const batch = await get("/api/workspace/matching/calculate", { method: "POST", headers: { ...headers, cookie: hr }, body: JSON.stringify(payload) });
  assert.equal(batch.status, 202);
  const detail = await get("/api/workspace/matching/" + matchId, { headers: { cookie: candidate } });
  assert.equal(detail.status, 200);
  const gap = await get("/api/workspace/matching/" + matchId + "/gap-analysis", { headers: { cookie: candidate } });
  assert.equal((await gap.json()).data.explanation, null);
  const page = await get("/matching", { headers: { cookie: candidate } });
  assert.equal(page.status, 200);
  const html = await page.text(); assert.match(html, /noindex, nofollow/); assert.doesNotMatch(html, /candidate-token/);
  const detailPage = await get("/matching/" + matchId, { headers: { cookie: candidate } });
  assert.equal(detailPage.status, 200); assert.match(await detailPage.text(), /noindex, nofollow/);
  const wrongRole = await get("/matching", { headers: { cookie: "cvinsight_session=admin-token" } });
  assert.equal(wrongRole.status, 307);
  const sitemap = await (await get("/sitemap.xml")).text();
  assert.doesNotMatch(sitemap, /matching|workspace/);
}
async function waitReady() {
  for (let i = 0; i < 100; i++) { try { const res = await get("/"); if (res.ok) return; } catch { /* starting */ } await new Promise((done) => setTimeout(done, 250)); }
  throw new Error("Next.js server not ready");
}
function build() {
  assert.ok(npmCli);
  const p = spawnSync(process.execPath, [npmCli, "run", "build"], { cwd: root, env: { ...process.env, BACKEND_API_URL: "http://127.0.0.1:" + backendPort, SITE_URL: "", SEO_INDEXING_ENABLED: "false" }, stdio: "inherit" });
  assert.equal(p.status, 0);
}
let app;
try {
  await new Promise((resolve, reject) => { backend.once("error", reject); backend.listen(backendPort, "127.0.0.1", resolve); });
  build();
  app = spawn(process.execPath, [nextBin, "start", "-p", String(appPort)], { cwd: root, env: { ...process.env, BACKEND_API_URL: "http://127.0.0.1:" + backendPort, SITE_URL: "", SEO_INDEXING_ENABLED: "false" }, stdio: "ignore" });
  await waitReady(); await verify();
  console.log("Production matching HTTP smoke PASS");
} finally {
  if (app) { app.kill(); await Promise.race([new Promise((resolve) => app.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 3000))]); }
  await new Promise((resolve) => backend.close(resolve));
}
