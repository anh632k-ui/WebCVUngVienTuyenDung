import assert from "node:assert/strict";
import test from "node:test";
import { MAX_RESUME_BYTES, isResumeFile, isUuid, validateParsedData } from "../src/lib/resume/contracts.ts";

const valid = {
  candidate_profile: { full_name: "Nguyễn Văn A", email: "a@example.test" },
  skills: [{ skill_id: 21, years_of_experience: 1.5, proficiency_level: "INTERMEDIATE" }],
  experiences: [{ company_name: "Công ty", job_title: "Dev", start_date: "2023-01-01", end_date: null, is_current: true, description: null }],
  educations: [{ institution_name: "Đại học", start_year: 2020, graduation_year: 2024, gpa: 3.2 }],
};
test("upload chỉ chấp nhận PDF/DOCX tối đa 5MB", () => {
  assert.equal(isResumeFile({ name: "cv.pdf", size: MAX_RESUME_BYTES }), true);
  assert.equal(isResumeFile({ name: "cv.DOCX", size: 512 }), true);
  assert.equal(isResumeFile({ name: "cv.exe", size: 512 }), false);
  assert.equal(isResumeFile({ name: "cv.pdf", size: MAX_RESUME_BYTES + 1 }), false);
  assert.equal(isResumeFile({ name: "cv.pdf", size: 0 }), false);
});
test("resume và idempotency key sử dụng UUID hợp lệ", () => {
  assert.ok(isUuid("00000000-0000-4000-8000-000000000001"));
  assert.equal(isUuid("../admin"), false);
  assert.equal(isUuid("not-a-uuid"), false);
});
test("parsed-data nhận đầy đủ profile/skills/experience/education", () => {
  assert.equal(validateParsedData(valid), true);
  assert.equal(validateParsedData({ candidate_profile: null, skills: [], experiences: [], educations: [] }), true);
});
test("parsed-data fail-closed khi có extra fields, duplicate skill hoặc sai thời gian", () => {
  assert.equal(validateParsedData({ ...valid, role: "ADMIN" }), false);
  assert.equal(validateParsedData({ ...valid, skills: [...valid.skills, ...valid.skills] }), false);
  assert.equal(validateParsedData({ ...valid, experiences: [{ ...valid.experiences[0], end_date: "2020-01-01", is_current: false }] }), false);
  assert.equal(validateParsedData({ ...valid, educations: [{ ...valid.educations[0], graduation_year: 2019 }] }), false);
  assert.equal(validateParsedData({ ...valid, skills: [{ skill_id: 3, years_of_experience: "1.2", proficiency_level: null }] }), false);
});
