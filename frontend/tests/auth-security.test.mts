import assert from "node:assert/strict";
import test from "node:test";
import { validateLogin, validatePasswordChange, validateProfile, validateRegistration } from "../src/lib/auth/contracts.ts";
import { dashboardForRole } from "../src/lib/auth/roles.ts";
import { sessionCookieOptions, validateMutationRequest } from "../src/lib/auth/security.ts";

test("registration chỉ chấp nhận Candidate/HR và loại confirmation khỏi payload", () => {
  const candidate = validateRegistration({ email: " USER@example.com ", password: "password123", full_name: " Nguyễn Văn A ", phone_number: "", role: "CANDIDATE" });
  assert.equal(candidate.ok, true);
  if (candidate.ok) assert.deepEqual(candidate.value, { email: "user@example.com", password: "password123", full_name: "Nguyễn Văn A", phone_number: null, role: "CANDIDATE" });
  assert.equal(validateRegistration({ email: "admin@example.com", password: "password123", full_name: "Admin", role: "ADMIN" }).ok, false);
  assert.equal(validateRegistration({ email: "x@example.com", password: "password123", full_name: "X", role: "HR", password_confirmation: "password123" }).ok, false);
});

test("login, profile và password payload fail closed với field ngoài contract", () => {
  assert.equal(validateLogin({ email: "user@example.com", password: "x" }).ok, true);
  assert.equal(validateProfile({ full_name: "A", phone_number: null, role: "ADMIN" }).ok, false);
  assert.equal(validateProfile({ full_name: "A", phone_number: "123" }).ok, true);
  assert.equal(validatePasswordChange({ current_password: "old", new_password: "12345678", token: "x" }).ok, false);
});

test("cookie session có flags bảo mật và TTL đúng backend", () => {
  assert.deepEqual(sessionCookieOptions(900, true), { httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 900, priority: "high" });
  assert.equal(sessionCookieOptions(900, false).secure, false);
});

test("CSRF chỉ nhận JSON mutation cùng origin", () => {
  const good = new Request("https://app.test/api/session/login", { method: "POST", headers: { Origin: "https://app.test", "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" } });
  const evil = new Request("https://app.test/api/session/login", { method: "POST", headers: { Origin: "https://evil.test", "Content-Type": "application/json", "Sec-Fetch-Site": "cross-site" } });
  assert.equal(validateMutationRequest(good), null);
  assert.match(validateMutationRequest(evil) ?? "", /khác nguồn/);
  assert.ok(validateMutationRequest(new Request("https://app.test/api", { method: "POST", headers: { Origin: "https://app.test", "Content-Type": "text/plain" } })));
  assert.ok(validateMutationRequest(new Request("https://app.test/api", { method: "POST", headers: { Origin: "https://app.test", "Content-Type": "application/json-p" } })));
  assert.ok(validateMutationRequest(new Request("https://app.test/api", { method: "POST", headers: { "Content-Type": "application/json" } })));
});

test("dashboard redirect chỉ dùng mapping role cố định", () => {
  assert.equal(dashboardForRole("CANDIDATE"), "/dashboard/candidate");
  assert.equal(dashboardForRole("HR"), "/dashboard/hr");
  assert.equal(dashboardForRole("ADMIN"), "/dashboard/admin");
});
