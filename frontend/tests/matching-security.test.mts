import assert from "node:assert/strict";
import test from "node:test";
import { isMatchUuid, safeScore, skillLabel, validCalculate } from "../src/lib/matching/contracts.ts";
const job = "00000000-0000-4000-8000-000000000011";
const cv = "00000000-0000-4000-8000-000000000022";
const cv2 = "00000000-0000-4000-8000-000000000033";
test("matching trigger validates UUID and role-limited batch size", () => {
  assert.ok(isMatchUuid(job));
  assert.equal(validCalculate({ job_id: job, resume_ids: [cv] }, "CANDIDATE"), true);
  assert.equal(validCalculate({ job_id: job, resume_ids: [cv, cv2] }, "HR"), true);
  assert.equal(validCalculate({ job_id: job, resume_ids: [cv, cv2] }, "CANDIDATE"), false);
  assert.equal(validCalculate({ job_id: job, resume_ids: [cv, cv] }, "HR"), false);
  assert.equal(validCalculate({ job_id: job, resume_ids: [] }, "HR"), false);
  assert.equal(validCalculate({ job_id: job, resume_ids: [cv], is_admin: true }, "HR"), false);
  assert.equal(validCalculate({ job_id: "../etc", resume_ids: [cv] }, "HR"), false);
});
test("unavailable scores are not treated as zero", () => {
  assert.equal(safeScore(null), null);
  assert.equal(safeScore(NaN), null);
  assert.equal(safeScore(-1), null);
  assert.equal(safeScore(101), null);
  assert.equal(safeScore(0), 0);
  assert.equal(safeScore(89.5), 89.5);
});
test("skill gap text handles canonical name and taxonomy ID", () => {
  assert.equal(skillLabel({ skill_name: "Python" }), "Python");
  assert.equal(skillLabel({ skill_id: 27 }), "Skill #27");
  assert.equal(skillLabel({}), "Kỹ năng chưa định danh");
});
