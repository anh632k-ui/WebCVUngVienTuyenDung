import assert from "node:assert/strict";
import test from "node:test";
import { adminUserQuery, validAdminPatch, validAdminUserId } from "../src/lib/admin/contracts.ts";
test("admin patch allowlist is_active, role Candidate/HR only", () => {
  assert.equal(validAdminPatch({ is_active: false }), true);
  assert.equal(validAdminPatch({ role: "CANDIDATE" }), true);
  assert.equal(validAdminPatch({ role: "HR", is_active: true }), true);
  assert.equal(validAdminPatch({ role: "ADMIN" }), false);
  assert.equal(validAdminPatch({ is_active: null }), false);
  assert.equal(validAdminPatch({ full_name: "Escalation" }), false);
  assert.equal(validAdminPatch({}), false);
});
test("admin filters constrained and UUID path required", () => {
  assert.equal(validAdminUserId("00000000-0000-4000-8000-000000000001"), true);
  assert.equal(validAdminUserId("../admin"), false);
  assert.equal(adminUserQuery(new URLSearchParams("role=ADMIN&is_active=false"))?.get("is_active"), "false");
  assert.equal(adminUserQuery(new URLSearchParams("role=SUPERUSER")), null);
  assert.equal(adminUserQuery(new URLSearchParams("is_active=maybe")), null);
  assert.equal(adminUserQuery(new URLSearchParams("limit=101")), null);
});
