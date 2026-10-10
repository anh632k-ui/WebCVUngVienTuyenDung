import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const nextBin = path.join(root, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath;
const backendPort = 3240, appPort = 3241;
const origin = `http://localhost:${appPort}`;
const cvId = "00000000-0000-4000-8000-000000000099";
const summary = { id: cvId, revision: 1, file_name: "profile.pdf", file_size: 512, mime_type: "application/pdf", parsing_status: "PARSED", is_manually_edited: false, created_at: "2026-01-01T00:00:00Z", parsed_at: "2026-01-01T00:00:02Z" };
const user = { id: "00000000-0000-4000-8000-000000000001", email: "candidate@example.test", full_name: "Candidate", phone_number: null, role: "CANDIDATE", is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
const detail = { resume: summary, candidate_profile: null, skills: [], experiences: [], educations: [] };
const hrCvId = "00000000-0000-4000-8000-000000000098";
const hrUser = { ...user, id: "00000000-0000-4000-8000-000000000002", email: "hr@example.test", full_name: "HR Test", role: "HR" };
const hrSummary = { ...summary, id: hrCvId, file_name: "hr-pool.pdf" };
const hrDetail = { resume: hrSummary, candidate_profile: null, skills: [], experiences: [], educations: [] };
const received = [];
function respond(res, status, data) { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(data)); }
async function read(req) { const chunks = []; for await (const c of req) chunks.push(c); return Buffer.concat(chunks); }
const backend = createServer(async (req, res) => {
  const bytes = await read(req);
  const pathname = new URL(req.url, "http://localhost").pathname;
  const auth = req.headers.authorization ?? "";
  received.push({ method: req.method, path: pathname, key: req.headers["idempotency-key"], auth, type: req.headers["content-type"], size: bytes.length });
  if (pathname === "/api/v1/users/me" && req.method === "GET") {
    return auth === "Bearer candidate-token"
      ? respond(res, 200, { success: true, data: user })
      : auth === "Bearer hr-token"
        ? respond(res, 200, { success: true, data: hrUser })
        : respond(res, 401, { success: false, error: { code: "INVALID_ACCESS_TOKEN", message: "Invalid token" } });
  }
  // Real FastAPI enforces resumes.owner_user_id; simulate two completely separate owners.
  if (auth === "Bearer hr-token") {
    if (pathname === "/api/v1/resumes" && req.method === "GET") return respond(res, 200, { success: true, data: [hrSummary], meta: { page: 1, limit: 10, total_items: 1, total_pages: 1 } });
    if (pathname === "/api/v1/resumes/upload" && req.method === "POST") return respond(res, 202, { success: true, data: { resume_id: hrCvId, revision: 1, file_name: "hr-pool.pdf", file_size: 512, parsing_status: "PENDING" } });
    if (pathname === `/api/v1/resumes/${hrCvId}` && req.method === "GET") return respond(res, 200, { success: true, data: hrDetail });
    if (pathname === `/api/v1/resumes/${hrCvId}/status` && req.method === "GET") return respond(res, 200, { success: true, data: { resume_id: hrCvId, revision: 1, parsing_status: "PARSED", parsed_at: summary.parsed_at, error_message: null } });
    if (pathname === `/api/v1/resumes/${hrCvId}/download` && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="hr-pool.pdf"', "Cache-Control": "private, no-store" });
      return res.end(Buffer.from("%PDF-1.4\n%%EOF"));
    }
    if (pathname === `/api/v1/resumes/${hrCvId}/parsed-data` && req.method === "PUT") return respond(res, 200, { success: true, data: { ...hrDetail, candidate_profile: JSON.parse(bytes.toString("utf8")).candidate_profile } });
    if (pathname === `/api/v1/resumes/${hrCvId}` && req.method === "DELETE") { res.writeHead(204); return res.end(); }
    return respond(res, 404, { success: false, error: { code: "NOT_FOUND", message: "CV not found" } });
  }
  if (auth !== "Bearer candidate-token") return respond(res, 403, { success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Forbidden" } });
  if (pathname === "/api/v1/resumes" && req.method === "GET") return respond(res, 200, { success: true, data: [summary], meta: { page: 1, limit: 10, total_items: 1, total_pages: 1 } });
  if (pathname === "/api/v1/resumes/upload" && req.method === "POST") return respond(res, 202, { success: true, data: { resume_id: cvId, revision: 1, file_name: "profile.pdf", file_size: 512, parsing_status: "PENDING" } });
  if (pathname === `/api/v1/resumes/${cvId}` && req.method === "GET") return respond(res, 200, { success: true, data: detail });
  if (pathname === `/api/v1/resumes/${cvId}/status` && req.method === "GET") return respond(res, 200, { success: true, data: { resume_id: cvId, revision: 1, parsing_status: "PARSED", parsed_at: summary.parsed_at, error_message: null } });
  if (pathname === `/api/v1/resumes/${cvId}/download` && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="profile.pdf"', "Cache-Control": "private, no-store" });
    return res.end(Buffer.from("%PDF-1.4\n%%EOF"));
  }
  if (pathname === `/api/v1/resumes/${cvId}/parsed-data` && req.method === "PUT") return respond(res, 200, { success: true, data: { ...detail, candidate_profile: JSON.parse(bytes.toString("utf8")).candidate_profile } });
  if (pathname === `/api/v1/resumes/${cvId}` && req.method === "DELETE") { res.writeHead(204); return res.end(); }
  return respond(res, 404, { success: false, error: { code: "NOT_FOUND", message: "CV not found" } });
});
function listen(port) { return new Promise((resolve, reject) => { backend.once("error", reject); backend.listen(port, "127.0.0.1", resolve); }); }
function close() { return new Promise((resolve) => backend.close(resolve)); }
function build() {
  assert.ok(npmCli);
  const p = spawnSync(process.execPath, [npmCli, "run", "build"], { cwd: root, env: { ...process.env, BACKEND_API_URL: `http://127.0.0.1:${backendPort}`, SEO_INDEXING_ENABLED: "false", SITE_URL: "" }, stdio: "inherit" });
  assert.equal(p.status, 0, "Next.js production build failed");
}
const headers = { origin, "content-type": "application/json", "sec-fetch-site": "same-origin" };
const cookie = "cvinsight_session=candidate-token";
async function req(route, init = {}) { return fetch(origin + route, { redirect: "manual", ...init }); }
async function check() {
  const guest = await req("/api/candidate/resumes");
  assert.equal(guest.status, 401);
  const hr = await req("/api/candidate/resumes", { headers: { cookie: "cvinsight_session=hr-token" } });
  assert.equal(hr.status, 403);
  const list = await req("/api/candidate/resumes?page=1&limit=10", { headers: { cookie } });
  assert.equal(list.status, 200);
  assert.match(list.headers.get("cache-control") ?? "", /no-store/);
  assert.equal((await list.json()).data[0].id, cvId);
  assert.equal((await req("/api/candidate/resumes?page=-1", { headers: { cookie } })).status, 422);
  const cvPage = await req("/cv", { headers: { cookie } });
  const html = await cvPage.text();
  assert.equal(cvPage.status, 200);
  assert.match(html, /Hồ sơ CV của tôi/);
  assert.match(html, /noindex, nofollow/);
  assert.doesNotMatch(html, /candidate-token/);
  const protectedDetail = await req(`/cv/${cvId}`, { headers: { cookie } });
  assert.equal(protectedDetail.status, 200);
  assert.match(await protectedDetail.text(), /noindex, nofollow/);
  const wrongRole = await req("/cv", { headers: { cookie: "cvinsight_session=hr-token" } });
  assert.equal(wrongRole.status, 307);
  const bad = await req("/api/candidate/resumes/not-an-id", { headers: { cookie } });
  assert.equal(bad.status, 422);
  const detailRes = await req(`/api/candidate/resumes/${cvId}`, { headers: { cookie } });
  assert.equal(detailRes.status, 200);
  assert.equal((await detailRes.json()).data.resume.file_name, "profile.pdf");
  const status = await req(`/api/candidate/resumes/${cvId}/status`, { headers: { cookie } });
  assert.equal((await status.json()).data.parsing_status, "PARSED");
  const pdf = await req(`/api/candidate/resumes/${cvId}/download`, { headers: { cookie } });
  assert.equal(pdf.status, 200);
  assert.match(pdf.headers.get("content-type") ?? "", /^application\/pdf/);
  assert.match(Buffer.from(await pdf.arrayBuffer()).toString("utf8"), /%PDF/);
  const form = new FormData();
  form.set("file", new Blob([Buffer.from("%PDF-1.4\n%%EOF")], { type: "application/pdf" }), "profile.pdf");
  const uuid = "c33d3333-3333-4333-8333-333333333333";
  const uploaded = await req("/api/candidate/resumes", { method: "POST", headers: { cookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": uuid }, body: form });
  assert.equal(uploaded.status, 202, await uploaded.clone().text());
  assert.equal(received.find((i) => i.path === "/api/v1/resumes/upload")?.key, uuid);
  const before = received.filter((i) => i.path === "/api/v1/resumes/upload").length;
  const malicious = await req("/api/candidate/resumes", { method: "POST", headers: { cookie, origin: "https://evil.example", "sec-fetch-site": "cross-site", "idempotency-key": uuid }, body: form });
  assert.equal(malicious.status, 403);
  assert.equal(received.filter((i) => i.path === "/api/v1/resumes/upload").length, before);
  const invalidUpload = await req("/api/candidate/resumes", { method: "POST", headers: { cookie, origin, "idempotency-key": "bad" }, body: form });
  assert.equal(invalidUpload.status, 422);

  // Regression: multipart boundary overhead must not change the 5 MiB *file* limit.
  // The BFF must reject oversized files with 413 before forwarding to FastAPI.
  const oversized = new FormData();
  oversized.set("file", new Blob([new Uint8Array(5 * 1024 * 1024 + 1)], { type: "application/pdf" }), "oversized.pdf");
  const uploadsBeforeOversized = received.filter((i) => i.path === "/api/v1/resumes/upload").length;
  const oversizedRes = await req("/api/candidate/resumes", {
    method: "POST", headers: { cookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": "c33d3333-3333-4333-8333-333333333334" }, body: oversized,
  });
  assert.equal(oversizedRes.status, 413, await oversizedRes.clone().text());
  assert.equal((await oversizedRes.json()).error.code, "FILE_TOO_LARGE");
  assert.equal(received.filter((i) => i.path === "/api/v1/resumes/upload").length, uploadsBeforeOversized);

  // Empty PDF is still a validation error, not a payload-too-large error.
  const empty = new FormData();
  empty.set("file", new Blob([], { type: "application/pdf" }), "empty.pdf");
  const emptyRes = await req("/api/candidate/resumes", {
    method: "POST", headers: { cookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": "c33d3333-3333-4333-8333-333333333335" }, body: empty,
  });
  assert.equal(emptyRes.status, 422, await emptyRes.clone().text());

  // Exactly 5 MiB is accepted by the BFF; mock FastAPI returns 202.
  const maxSize = new FormData();
  maxSize.set("file", new Blob([new Uint8Array(5 * 1024 * 1024)], { type: "application/pdf" }), "max-size.pdf");
  const maxRes = await req("/api/candidate/resumes", {
    method: "POST", headers: { cookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": "c33d3333-3333-4333-8333-333333333336" }, body: maxSize,
  });
  assert.equal(maxRes.status, 202, await maxRes.clone().text());
  const update = await req(`/api/candidate/resumes/${cvId}/parsed-data`, { method: "PUT", headers: { ...headers, cookie }, body: JSON.stringify({ candidate_profile: null, skills: [], experiences: [], educations: [] }) });
  assert.equal(update.status, 200, await update.clone().text());
  const escalation = await req(`/api/candidate/resumes/${cvId}/parsed-data`, { method: "PUT", headers: { ...headers, cookie }, body: JSON.stringify({ candidate_profile: null, skills: [], experiences: [], educations: [], role: "ADMIN" }) });
  assert.equal(escalation.status, 422);
  const removed = await req(`/api/candidate/resumes/${cvId}`, { method: "DELETE", headers: { ...headers, cookie }, body: "{}" });
  assert.equal(removed.status, 204);
  // HR talent pool is independent of Candidate CVs, even when using the same FastAPI endpoints.
  const hrCookie = "cvinsight_session=hr-token";
  const guestHr = await req("/api/hr/resumes");
  assert.equal(guestHr.status, 401);
  const candidateAtHr = await req("/api/hr/resumes", { headers: { cookie } });
  assert.equal(candidateAtHr.status, 403);
  const hrList = await req("/api/hr/resumes", { headers: { cookie: hrCookie } });
  assert.equal(hrList.status, 200);
  assert.match(hrList.headers.get("cache-control") ?? "", /no-store/);
  const hrItems = (await hrList.json()).data;
  assert.deepEqual(hrItems.map((item) => item.id), [hrCvId]);
  const hrPage = await req("/hr/talent-pool", { headers: { cookie: hrCookie } });
  assert.equal(hrPage.status, 200);
  const hrHtml = await hrPage.text();
  assert.match(hrHtml, /Talent pool của tôi/);
  assert.match(hrHtml, /noindex, nofollow/);
  assert.match(hrPage.headers.get("cache-control") ?? "", /no-store/);
  assert.doesNotMatch(hrHtml, /hr-token|candidate-token/);
  const forbiddenCandidateHrPage = await req("/hr/talent-pool", { headers: { cookie }, redirect: "manual" });
  assert.equal(forbiddenCandidateHrPage.status, 307);
  const forbiddenGuestHrPage = await req("/hr/talent-pool");
  assert.equal(forbiddenGuestHrPage.status, 307); // redirect: manual; never serve protected HTML
  assert.equal(new URL(forbiddenGuestHrPage.headers.get("location"), origin).pathname, "/dang-nhap");
  assert.doesNotMatch(await forbiddenGuestHrPage.text(), /Talent pool của tôi/);
  const hrDetailPage = await req(`/hr/talent-pool/${hrCvId}`, { headers: { cookie: hrCookie } });
  assert.equal(hrDetailPage.status, 200);
  assert.match(await hrDetailPage.text(), /noindex, nofollow/);
  const candidateReadsHr = await req(`/api/candidate/resumes/${hrCvId}`, { headers: { cookie } });
  assert.equal(candidateReadsHr.status, 404);
  const hrReadsCandidate = await req(`/api/hr/resumes/${cvId}`, { headers: { cookie: hrCookie } });
  assert.equal(hrReadsCandidate.status, 404);
  const hrDetailRes = await req(`/api/hr/resumes/${hrCvId}`, { headers: { cookie: hrCookie } });
  assert.equal(hrDetailRes.status, 200);
  assert.equal((await hrDetailRes.json()).data.resume.id, hrCvId);
  const hrStatus = await req(`/api/hr/resumes/${hrCvId}/status`, { headers: { cookie: hrCookie } });
  assert.equal(hrStatus.status, 200);
  assert.equal((await hrStatus.json()).data.parsing_status, "PARSED");
  const hrDownload = await req(`/api/hr/resumes/${hrCvId}/download`, { headers: { cookie: hrCookie } });
  assert.equal(hrDownload.status, 200);
  assert.match(hrDownload.headers.get("content-type") ?? "", /application\/pdf/);
  const hrEdit = await req(`/api/hr/resumes/${hrCvId}/parsed-data`, {
    method: "PUT", headers: { ...headers, cookie: hrCookie },
    body: JSON.stringify({ candidate_profile: null, skills: [], experiences: [], educations: [] }),
  });
  assert.equal(hrEdit.status, 200);
  const hrUpload = new FormData();
  hrUpload.set("file", new Blob([Buffer.from("%PDF-1.4\n%%EOF")], { type: "application/pdf" }), "hr-pool.pdf");
  const hrUploadKey = "c33d3333-3333-4333-8333-333333333337";
  const hrUploadResult = await req("/api/hr/resumes", { method: "POST", headers: { cookie: hrCookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": hrUploadKey }, body: hrUpload });
  assert.equal(hrUploadResult.status, 202, await hrUploadResult.clone().text());
  assert.doesNotMatch(JSON.stringify(await hrUploadResult.json()), /hr-token/);
  assert.ok(received.some((i) => i.path === "/api/v1/resumes/upload" && i.auth === "Bearer hr-token" && i.key === hrUploadKey));
  const countHrUploads = received.filter((i) => i.path === "/api/v1/resumes/upload" && i.auth === "Bearer hr-token").length;
  const hrCrossSite = await req("/api/hr/resumes", { method: "POST", headers: { cookie: hrCookie, origin: "https://evil.example", "sec-fetch-site": "cross-site", "idempotency-key": hrUploadKey }, body: hrUpload });
  assert.equal(hrCrossSite.status, 403);
  assert.equal(received.filter((i) => i.path === "/api/v1/resumes/upload" && i.auth === "Bearer hr-token").length, countHrUploads);
  const candidateTriesHrUpload = await req("/api/hr/resumes", { method: "POST", headers: { cookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": hrUploadKey }, body: hrUpload });
  assert.equal(candidateTriesHrUpload.status, 403);
  const invalidHrKey = await req("/api/hr/resumes", { method: "POST", headers: { cookie: hrCookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": "invalid" }, body: hrUpload });
  assert.equal(invalidHrKey.status, 422);
  const oversizedHr = new FormData();
  oversizedHr.set("file", new Blob([new Uint8Array(5 * 1024 * 1024 + 1)], { type: "application/pdf" }), "oversized.pdf");
  const rejectedHr = await req("/api/hr/resumes", { method: "POST", headers: { cookie: hrCookie, origin, "sec-fetch-site": "same-origin", "idempotency-key": "c33d3333-3333-4333-8333-333333333338" }, body: oversizedHr });
  assert.equal(rejectedHr.status, 413);
  assert.equal(received.filter((i) => i.path === "/api/v1/resumes/upload" && i.auth === "Bearer hr-token").length, countHrUploads);
  const deletedHr = await req(`/api/hr/resumes/${hrCvId}`, { method: "DELETE", headers: { ...headers, cookie: hrCookie }, body: "{}" });
  assert.equal(deletedHr.status, 204);
  const site = await (await req("/sitemap.xml")).text();
  assert.doesNotMatch(site, /\/cv\b|\/api\/candidate|\/hr\/talent-pool|\/api\/hr\/resumes/i);
}
async function waitReady() {
  for (let i = 0; i < 80; i++) {
    try { const result = await req("/"); if (result.ok) return; } catch { /* start */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Next production server was not ready");
}
let app;
try {
  await listen(backendPort);
  build();
  app = spawn(process.execPath, [nextBin, "start", "-p", String(appPort)], {
    cwd: root, env: { ...process.env, BACKEND_API_URL: `http://127.0.0.1:${backendPort}`, SEO_INDEXING_ENABLED: "false", SITE_URL: "" }, stdio: "ignore",
  });
  await waitReady();
  await check();
  console.log("Production resume BFF smoke PASS");
} finally {
  if (app) { app.kill(); await Promise.race([new Promise((resolve) => app.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 3000))]); }
  await close();
}
