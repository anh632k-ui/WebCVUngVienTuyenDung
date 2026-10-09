import assert from "node:assert/strict";
import test from "node:test";
import { currentDashboardRoute, getDashboardLinks } from "../src/lib/navigation/dashboard-links.ts";

test("dashboard shortcuts only expose the current role's module routes", () => {
  assert.deepEqual(getDashboardLinks("CANDIDATE").map((link) => link.href), ["/cv", "/matching"]);
  assert.deepEqual(getDashboardLinks("HR").map((link) => link.href), ["/hr/jobs", "/matching"]);
  assert.deepEqual(getDashboardLinks("ADMIN").map((link) => link.href), ["/admin/users"]);
  assert.ok(!getDashboardLinks("CANDIDATE").some((link) => link.href.startsWith("/admin")));
});

test("active sidebar route compares exact dashboard and nested paths", () => {
  assert.equal(currentDashboardRoute("/cv/a", "/cv"), true);
  assert.equal(currentDashboardRoute("/hr/jobs/1/leaderboard", "/hr/jobs"), true);
  assert.equal(currentDashboardRoute("/matching/a", "/matching"), true);
  assert.equal(currentDashboardRoute("/dashboard/hr", "/dashboard/hr"), true);
  assert.equal(currentDashboardRoute("/dashboard/hr/stats", "/dashboard/hr"), false);
  assert.equal(currentDashboardRoute("/administrator", "/admin"), false);
});
