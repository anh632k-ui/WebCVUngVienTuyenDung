import assert from "node:assert/strict";
import test from "node:test";
import { validCriteria, validJobCreate, validJobId, validJobStatus, validJobUpdate, validWeights } from "../src/lib/jobs/contracts.ts";

test("JD identity must be UUID; creation accepts title/level/body and exact weights", () => {
  assert.ok(validJobId("00000000-0000-4000-8000-000000000099"));
  assert.equal(validJobId("../admin"), false);
  assert.equal(validJobCreate({ title: "Backend Developer", job_level: "Junior", raw_content: "Python and FastAPI" }), true);
  assert.equal(validJobCreate({ title: "JD", job_level: "Junior", raw_content: "Python", role: "ADMIN" }), false);
  assert.equal(validJobCreate({ title: "JD", job_level: "Junior", raw_content: "Python", w_skill: 0.6, w_semantic: 0.3, w_experience: 0.2 }), false);
});
test("weights allow precise milli values and reject overprecision/duplicate mutation field", () => {
  assert.equal(validWeights({ w_skill: 0.501, w_semantic: 0.299, w_experience: 0.2, recalculate: true }), true);
  assert.equal(validWeights({ w_skill: 0.5001, w_semantic: 0.2999, w_experience: 0.2 }), false);
  assert.equal(validWeights({ w_skill: 0.5, w_semantic: 0.3, w_experience: 0.2, recalculate: "true" }), false);
  assert.equal(validWeights({ w_skill: 0.5, w_semantic: 0.3, w_experience: 0.2, admin: true }), false);
});
test("criteria requires taxonomy IDs and valid ranges; update/status fail closed", () => {
  const criteria = { min_experience_years: 2, education_requirement: null, skills: [{ skill_id: 2, importance: "MANDATORY", min_years_required: 1 }] };
  assert.equal(validCriteria(criteria), true);
  assert.equal(validCriteria({ ...criteria, skills: [...criteria.skills, ...criteria.skills] }), false);
  assert.equal(validCriteria({ ...criteria, skills: [] }), false);
  assert.equal(validCriteria({ ...criteria, min_experience_years: 1.26 }), false);
  assert.equal(validJobUpdate({ title: "Updated" }), true);
  assert.equal(validJobUpdate({ role: "ADMIN" }), false);
  assert.equal(validJobStatus({ status: "ACTIVE" }), true);
  assert.equal(validJobStatus({ status: "ADMIN" }), false);
});
