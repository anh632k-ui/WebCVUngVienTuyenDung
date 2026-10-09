import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import process from "node:process";

const projectRoot = process.cwd();
const nextBin = path.join(projectRoot, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath;
const backendPort = 3220;
const frontendPort = 3221;
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://localhost:${frontendPort}`;
const received = [];

const baseUser = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "candidate@example.test",
  full_name: "Candidate Test",
  phone_number: null,
  role: "CANDIDATE",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

function tokenUser(authorization) {
  const token = authorization?.replace(/^Bearer /, "");
  if (token === "candidate-token") return baseUser;
  if (token === "hr-token") return { ...baseUser, id: "00000000-0000-4000-8000-000000000002", email: "hr@example.test", full_name: "HR Test", role: "HR" };
  if (token === "admin-token") return { ...baseUser, id: "00000000-0000-4000-8000-000000000003", email: "admin@example.test", full_name: "Admin Test", role: "ADMIN" };
  return null;
}

async function bodyOf(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : null;
}

const backend = createServer(async (request, response) => {
  const body = await bodyOf(request);
  received.push({ method: request.method, url: request.url, authorization: request.headers.authorization, body });

  if (request.method === "POST" && request.url === "/api/v1/auth/register") {
    return json(response, 201, { success: true, data: { ...baseUser, ...body, password: undefined } });
  }
  if (request.method === "POST" && request.url === "/api/v1/auth/login") {
    if (body.email === "bad@example.test") return json(response, 401, { success: false, error: { code: "INVALID_CREDENTIALS", message: "Invalid credentials" } });
    if (body.email === "inactive@example.test") return json(response, 403, { success: false, error: { code: "ACCOUNT_INACTIVE", message: "Inactive" } });
    const role = body.email.startsWith("admin") ? "ADMIN" : body.email.startsWith("hr") ? "HR" : "CANDIDATE";
    const token = `${role.toLowerCase()}-token`;
    const user = tokenUser(`Bearer ${token}`);
    return json(response, 200, { success: true, data: { access_token: token, token_type: "bearer", expires_in: 900, user } });
  }
  if (request.url === "/api/v1/users/me" && request.method === "GET") {
    const user = tokenUser(request.headers.authorization);
    return user
      ? json(response, 200, { success: true, data: user })
      : json(response, 401, { success: false, error: { code: "TOKEN_INVALID", message: "Invalid token" } });
  }
  if (request.url === "/api/v1/users/me" && request.method === "PUT") {
    const user = tokenUser(request.headers.authorization);
    return user
      ? json(response, 200, { success: true, data: { ...user, ...body, updated_at: "2026-01-02T00:00:00Z" } })
      : json(response, 401, { success: false, error: { code: "TOKEN_INVALID", message: "Invalid token" } });
  }
  if (request.url === "/api/v1/auth/change-password" && request.method === "PUT") {
    if (!tokenUser(request.headers.authorization)) return json(response, 401, { success: false, error: { code: "TOKEN_INVALID", message: "Invalid token" } });
    if (body.current_password === "wrong-password") return json(response, 400, { success: false, error: { code: "CURRENT_PASSWORD_INCORRECT", message: "Incorrect" } });
    return json(response, 200, { success: true, data: { message: "Password changed" } });
  }
  return json(response, 404, { success: false, error: { code: "NOT_FOUND", message: "Not found" } });
});

function listen(server, port) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function build(environment) {
  assert.ok(npmCli, "npm_execpath must exist when this test runs through npm");
  const result = spawnSync(process.execPath, [npmCli, "run", "build"], {
    cwd: projectRoot,
    env: { ...process.env, ...environment },
    stdio: "inherit",
  });
  assert.equal(result.status, 0, "next build must succeed");
}

async function waitUntilReady(logs) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(frontendUrl);
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Production server did not start.\n${logs()}`);
}

function mutationHeaders(extra = {}) {
  return { "content-type": "application/json", origin: frontendUrl, "sec-fetch-site": "same-origin", ...extra };
}

async function mutation(route, body, { method = "POST", cookie, origin = frontendUrl } = {}) {
  return fetch(`${frontendUrl}${route}`, {
    method,
    headers: mutationHeaders({ ...(cookie ? { cookie } : {}), origin }),
    body: JSON.stringify(body),
    redirect: "manual",
  });
}

function cookieFrom(response) {
  const setCookie = response.headers.get("set-cookie");
  assert.ok(setCookie, "response must set a cookie");
  return setCookie.split(";", 1)[0];
}

async function login(email) {
  const response = await mutation("/api/session/login", { email, password: "correct-password" });
  return { response, body: await response.json() };
}

async function verify() {
  const registerCandidate = await mutation("/api/session/register", { email: "new@example.test", password: "password-123", full_name: "New User", phone_number: null, role: "CANDIDATE" });
  assert.equal(registerCandidate.status, 201, await registerCandidate.clone().text());
  assert.equal(registerCandidate.headers.get("set-cookie"), null);
  const registerHr = await mutation("/api/session/register", { email: "new-hr@example.test", password: "password-123", full_name: "New HR", phone_number: "0123", role: "HR" });
  assert.equal(registerHr.status, 201);
  const registrations = received.filter((entry) => entry.url === "/api/v1/auth/register");
  assert.deepEqual(registrations.map((entry) => entry.body.role), ["CANDIDATE", "HR"]);

  const beforeAdmin = registrations.length;
  const registerAdmin = await mutation("/api/session/register", { email: "new-admin@example.test", password: "password-123", full_name: "New Admin", phone_number: null, role: "ADMIN" });
  assert.equal(registerAdmin.status, 422);
  assert.equal(received.filter((entry) => entry.url === "/api/v1/auth/register").length, beforeAdmin);

  const candidateLogin = await login("candidate@example.test");
  assert.equal(candidateLogin.response.status, 200);
  assert.equal(candidateLogin.body.data.user.role, "CANDIDATE");
  assert.equal("access_token" in candidateLogin.body.data, false);
  assert.doesNotMatch(JSON.stringify(candidateLogin.body), /candidate-token/);
  const setCookie = candidateLogin.response.headers.get("set-cookie") ?? "";
  assert.match(setCookie, /^cvinsight_session=candidate-token;/);
  assert.match(setCookie, /HttpOnly/i);
  assert.match(setCookie, /Secure/i);
  assert.match(setCookie, /SameSite=Lax/i);
  assert.match(setCookie, /Path=\//i);
  assert.match(setCookie, /Max-Age=900/i);
  assert.doesNotMatch(setCookie, /Domain=/i);
  const candidateCookie = cookieFrom(candidateLogin.response);

  const badLogin = await login("bad@example.test");
  assert.equal(badLogin.response.status, 401);
  assert.equal(badLogin.response.headers.get("set-cookie"), null);
  const inactiveLogin = await login("inactive@example.test");
  assert.equal(inactiveLogin.response.status, 403);
  assert.equal(inactiveLogin.response.headers.get("set-cookie"), null);

  const loginsBeforeCsrf = received.filter((entry) => entry.url === "/api/v1/auth/login").length;
  const crossOrigin = await mutation("/api/session/login", { email: "candidate@example.test", password: "correct-password" }, { origin: "https://evil.example" });
  assert.equal(crossOrigin.status, 403);
  assert.equal(received.filter((entry) => entry.url === "/api/v1/auth/login").length, loginsBeforeCsrf);

  const me = await fetch(`${frontendUrl}/api/session/me`, { headers: { cookie: candidateCookie } });
  assert.equal(me.status, 200);
  assert.match(me.headers.get("cache-control") ?? "", /no-store/);
  assert.equal((await me.json()).data.role, "CANDIDATE");
  assert.equal(received.at(-1).authorization, "Bearer candidate-token");

  const expired = await fetch(`${frontendUrl}/api/session/me`, { headers: { cookie: "cvinsight_session=expired-token" } });
  assert.equal(expired.status, 401);
  assert.match(expired.headers.get("set-cookie") ?? "", /Max-Age=0/i);

  const profile = await mutation("/api/session/profile", { full_name: "Updated Name", phone_number: "0999" }, { method: "PUT", cookie: candidateCookie });
  assert.equal(profile.status, 200);
  assert.deepEqual(received.at(-1).body, { full_name: "Updated Name", phone_number: "0999" });
  const profileCalls = received.filter((entry) => entry.method === "PUT" && entry.url === "/api/v1/users/me").length;
  const profileEscalation = await mutation("/api/session/profile", { full_name: "Admin Now", phone_number: null, role: "ADMIN" }, { method: "PUT", cookie: candidateCookie });
  assert.equal(profileEscalation.status, 422);
  assert.equal(received.filter((entry) => entry.method === "PUT" && entry.url === "/api/v1/users/me").length, profileCalls);

  const wrongPassword = await mutation("/api/session/change-password", { current_password: "wrong-password", new_password: "new-password-123" }, { method: "PUT", cookie: candidateCookie });
  assert.equal(wrongPassword.status, 400);
  assert.equal(wrongPassword.headers.get("set-cookie"), null);
  const changedPassword = await mutation("/api/session/change-password", { current_password: "correct-password", new_password: "new-password-123" }, { method: "PUT", cookie: candidateCookie });
  assert.equal(changedPassword.status, 200);
  assert.match(changedPassword.headers.get("set-cookie") ?? "", /Max-Age=0/i);

  const logout = await mutation("/api/session/logout", {}, { cookie: candidateCookie });
  assert.equal(logout.status, 200);
  assert.match(logout.headers.get("set-cookie") ?? "", /Max-Age=0/i);

  const guest = await fetch(`${frontendUrl}/dashboard/candidate`, { redirect: "manual" });
  assert.equal(guest.status, 307);
  assert.equal(new URL(guest.headers.get("location"), frontendUrl).pathname, "/dang-nhap");
  const candidatePage = await fetch(`${frontendUrl}/dashboard/candidate`, { headers: { cookie: candidateCookie } });
  const candidateHtml = await candidatePage.text();
  assert.equal(candidatePage.status, 200);
  assert.match(candidateHtml, /self-matching/);
  assert.match(candidateHtml, /noindex, nofollow/);
  assert.doesNotMatch(candidateHtml, /candidate-token/);
  const wrongRole = await fetch(`${frontendUrl}/dashboard/admin`, { headers: { cookie: candidateCookie }, redirect: "manual" });
  assert.equal(wrongRole.status, 307);
  assert.equal(new URL(wrongRole.headers.get("location"), frontendUrl).pathname, "/dashboard/candidate");

  for (const [email, route, marker] of [["hr@example.test", "/dashboard/hr", "talent pool"], ["admin@example.test", "/dashboard/admin", "Admin"]]) {
    const session = await login(email);
    const page = await fetch(`${frontendUrl}${route}`, { headers: { cookie: cookieFrom(session.response) } });
    const html = await page.text();
    assert.equal(page.status, 200);
    assert.match(html, new RegExp(marker, "i"));
    assert.doesNotMatch(html, new RegExp(`${email.split("@")[0]}-token`));
  }

  const loginPage = await fetch(`${frontendUrl}/dang-nhap?next=https://evil.example`);
  const loginHtml = await loginPage.text();
  assert.equal(loginPage.status, 200);
  assert.match(loginHtml, /noindex, nofollow/);
  assert.doesNotMatch(loginHtml, /href="https:\/\/evil\.example/);
  const sitemap = await (await fetch(`${frontendUrl}/sitemap.xml`)).text();
  assert.doesNotMatch(sitemap, /dang-nhap|dang-ky|dashboard|tai-khoan|doi-mat-khau/);
}

const environment = { BACKEND_API_URL: backendUrl, SITE_URL: "https://auth-fixture.test", SEO_INDEXING_ENABLED: "true" };
let nextServer;
let logs = "";
try {
  await listen(backend, backendPort);
  build(environment);
  nextServer = spawn(process.execPath, [nextBin, "start", "-p", String(frontendPort)], {
    cwd: projectRoot,
    env: { ...process.env, ...environment },
    stdio: ["ignore", "pipe", "pipe"],
  });
  nextServer.stdout.on("data", (chunk) => { logs += chunk.toString(); });
  nextServer.stderr.on("data", (chunk) => { logs += chunk.toString(); });
  await waitUntilReady(() => logs);
  await verify();
  console.log("Production HTTP auth/BFF smoke: PASS");
} finally {
  if (nextServer) {
    nextServer.kill();
    await Promise.race([new Promise((resolve) => nextServer.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 3000))]);
  }
  await close(backend);
}
