export type JobStatus = "DRAFT" | "ACTIVE" | "CLOSED";
export type JobParsingStatus = "PENDING" | "PROCESSING" | "PARSED" | "FAILED";
export type JobData = {
  id: string; recruiter_id: string; revision: number; title: string; job_level: string; location: string | null;
  raw_content: string; min_experience_years: number; education_requirement: string | null;
  parsing_status: JobParsingStatus; is_criteria_verified: boolean;
  w_skill: number; w_semantic: number; w_experience: number;
  status: JobStatus; created_at: string; updated_at: string; parsed_at: string | null;
};
export type JobCriteria = {
  job_id: string; revision: number; min_experience_years: number;
  education_requirement: string | null; is_criteria_verified: boolean;
  skills: { skill_id: number; importance: "MANDATORY" | "OPTIONAL"; min_years_required: number }[];
};
export type JobPage = { success: true; data: JobData[]; meta: { page: number; limit: number; total_items: number; total_pages: number } };
export type JobResponse = { success: true; data: JobData };
export type CriteriaResponse = { success: true; data: JobCriteria };
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function validJobId(value: string) { return UUID_RE.test(value); }
function record(value: unknown): Record<string, unknown> | null { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null; }
function onlyKeys(value: Record<string, unknown>, keys: string[]) { return Object.keys(value).every((k) => keys.includes(k)); }
function text(value: unknown, max: number, required = false) {
  return typeof value === "string" && value.length <= max && !value.includes("\0") && (!required || value.trim().length > 0);
}
function optionalText(value: unknown, max: number) { return value === null || text(value, max); }
function milli(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) return null;
  const n = Math.round(value * 1000);
  return Math.abs(value * 1000 - n) < 1e-7 ? n : null;
}
export function validWeights(value: unknown, requiresAll = true) {
  const v = record(value);
  if (!v || !onlyKeys(v, ["w_skill", "w_semantic", "w_experience", "recalculate"])) return false;
  if ("recalculate" in v && typeof v.recalculate !== "boolean") return false;
  const present = ["w_skill", "w_semantic", "w_experience"].filter((key) => key in v);
  if (requiresAll && present.length !== 3) return false;
  if (!present.length) return true;
  if (present.length !== 3) return false;
  const vals = present.map((key) => milli(v[key]));
  return vals.every((n) => n !== null) && vals.reduce<number>((sum, n) => sum + (n ?? 0), 0) === 1000;
}
export function validJobCreate(value: unknown) {
  const v = record(value);
  if (!v || !onlyKeys(v, ["title", "job_level", "location", "raw_content", "w_skill", "w_semantic", "w_experience"])) return false;
  if (!text(v.title, 200, true) || !text(v.job_level, 50, true) || !text(v.raw_content, 150000, true)) return false;
  if (v.location !== undefined && !optionalText(v.location, 150)) return false;
  const weights = { w_skill: v.w_skill ?? 0.5, w_semantic: v.w_semantic ?? 0.3, w_experience: v.w_experience ?? 0.2 };
  return validWeights(weights);
}
export function validJobUpdate(value: unknown) {
  const v = record(value);
  if (!v || !Object.keys(v).length || !onlyKeys(v, ["title", "job_level", "location", "raw_content"])) return false;
  return (!("title" in v) || text(v.title, 200, true)) &&
    (!("job_level" in v) || text(v.job_level, 50, true)) &&
    (!("location" in v) || optionalText(v.location, 150)) &&
    (!("raw_content" in v) || text(v.raw_content, 150000, true));
}
export function validJobStatus(value: unknown): value is { status: JobStatus } {
  const v = record(value);
  return !!v && onlyKeys(v, ["status"]) && (v.status === "DRAFT" || v.status === "ACTIVE" || v.status === "CLOSED");
}
export function validCriteria(value: unknown) {
  const v = record(value);
  if (!v || !onlyKeys(v, ["min_experience_years", "education_requirement", "skills"]) || !Array.isArray(v.skills) || !v.skills.length || v.skills.length > 200) return false;
  if (v.education_requirement !== undefined && !optionalText(v.education_requirement, 255)) return false;
  const years = v.min_experience_years ?? 0;
  if (typeof years !== "number" || !Number.isFinite(years) || years < 0 || years > 999.9 || Math.abs(years * 10 - Math.round(years * 10)) > 1e-8) return false;
  const ids = new Set<number>();
  for (const skill of v.skills) {
    const s = record(skill);
    if (!s || !onlyKeys(s, ["skill_id", "importance", "min_years_required"]) ||
      typeof s.skill_id !== "number" || !Number.isSafeInteger(s.skill_id) || s.skill_id <= 0 || ids.has(s.skill_id) ||
      (s.importance !== "MANDATORY" && s.importance !== "OPTIONAL")) return false;
    const y = s.min_years_required ?? 0;
    if (typeof y !== "number" || !Number.isFinite(y) || y < 0 || y > 999.9 || Math.abs(y * 10 - Math.round(y * 10)) > 1e-8) return false;
    ids.add(s.skill_id);
  }
  return true;
}
