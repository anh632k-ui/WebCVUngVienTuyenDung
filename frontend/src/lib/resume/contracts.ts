export const RESUME_STATUSES = ["PENDING", "PROCESSING", "PARSED", "FAILED"] as const;
export type ParsingStatus = (typeof RESUME_STATUSES)[number];
export type ResumeSummary = {
  id: string; revision: number; file_name: string; file_size: number; mime_type: string;
  parsing_status: ParsingStatus; is_manually_edited: boolean; created_at: string; parsed_at: string | null;
};
export type ResumeStatus = { resume_id: string; revision: number; parsing_status: ParsingStatus; parsed_at: string | null; error_message: string | null };
export type CandidateProfile = {
  full_name: string | null; email: string | null; phone_number: string | null; current_title: string | null;
  location: string | null; linkedin_url: string | null; github_url: string | null; professional_summary: string | null;
};
export type ResumeSkill = { skill_id: number; years_of_experience: number | null; proficiency_level: string | null };
export type Experience = { company_name: string; job_title: string; start_date: string | null; end_date: string | null; is_current: boolean; description: string | null };
export type Education = { institution_name: string; degree: string | null; field_of_study: string | null; start_year: number | null; graduation_year: number | null; gpa: number | null; description: string | null };
export type ResumeDetail = { resume: ResumeSummary; candidate_profile: CandidateProfile | null; skills: ResumeSkill[]; experiences: Experience[]; educations: Education[] };
export type ResumePage = { success: true; data: ResumeSummary[]; meta: { page: number; limit: number; total_items: number; total_pages: number } };
export type ResumeDataResponse = { success: true; data: ResumeDetail };
export type ResumeStatusResponse = { success: true; data: ResumeStatus };
export type ParsedDataUpdate = { candidate_profile: CandidateProfile | null; skills: ResumeSkill[]; experiences: Experience[]; educations: Education[] };
export const MAX_RESUME_BYTES = 5 * 1024 * 1024;
const UUID_RE = /^[a-f\d]{8}-[a-f\d]{4}-[1-8][a-f\d]{3}-[89ab][a-f\d]{3}-[a-f\d]{12}$/i;
export function isUuid(value: string) { return UUID_RE.test(value); }
export function isParsingStatus(value: string): value is ParsingStatus { return RESUME_STATUSES.some((status) => status === value); }
export function isResumeFile(file: { name: string; size: number }) {
  return file.size > 0 && file.size <= MAX_RESUME_BYTES && /\.(pdf|docx)$/i.test(file.name);
}
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function knownKeys(value: Record<string, unknown>, keys: readonly string[]) {
  return Object.keys(value).every((key) => keys.includes(key));
}
function optionalText(value: unknown, maxLength: number): value is string | null {
  return value === null || (typeof value === "string" && value.length <= maxLength && !value.includes("\0"));
}
function nullableFinite(value: unknown, min = -Infinity, max = Infinity): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= min && value <= max);
}
function isoDate(value: unknown) {
  return optionalText(value, 10) && (value === null || /^\d{4}-\d{2}-\d{2}$/.test(value));
}
export function validateParsedData(input: unknown): input is ParsedDataUpdate {
  const body = record(input);
  if (!body || !knownKeys(body, ["candidate_profile", "skills", "experiences", "educations"]) ||
    !("candidate_profile" in body) || !Array.isArray(body.skills) || !Array.isArray(body.experiences) || !Array.isArray(body.educations) ||
    body.skills.length > 100 || body.experiences.length > 100 || body.educations.length > 100) return false;
  if (body.candidate_profile !== null) {
    const p = record(body.candidate_profile);
    if (!p || !knownKeys(p, ["full_name", "email", "phone_number", "current_title", "location", "linkedin_url", "github_url", "professional_summary"])) return false;
    for (const [key, max] of Object.entries({ full_name: 150, email: 255, phone_number: 30, current_title: 150, location: 150, linkedin_url: 500, github_url: 500, professional_summary: 10000 })) {
      if (!optionalText(p[key] ?? null, max)) return false;
    }
  }
  const seen = new Set<number>();
  for (const item of body.skills) {
    const s = record(item);
    if (!s || !knownKeys(s, ["skill_id", "years_of_experience", "proficiency_level"]) || typeof s.skill_id !== "number" ||
      !Number.isSafeInteger(s.skill_id) || s.skill_id <= 0 || seen.has(s.skill_id) ||
      !nullableFinite(s.years_of_experience ?? null, 0, 999.9) || !optionalText(s.proficiency_level ?? null, 30)) return false;
    seen.add(s.skill_id);
  }
  for (const item of body.experiences) {
    const e = record(item);
    if (!e || !knownKeys(e, ["company_name", "job_title", "start_date", "end_date", "is_current", "description"]) ||
      typeof e.company_name !== "string" || e.company_name.length > 150 ||
      typeof e.job_title !== "string" || e.job_title.length > 150 ||
      !isoDate(e.start_date ?? null) || !isoDate(e.end_date ?? null) ||
      typeof e.is_current !== "boolean" || !optionalText(e.description ?? null, 10000)) return false;
    if (e.is_current && e.end_date != null) return false;
    if (typeof e.start_date === "string" && typeof e.end_date === "string" && e.end_date < e.start_date) return false;
  }
  for (const item of body.educations) {
    const e = record(item);
    if (!e || !knownKeys(e, ["institution_name", "degree", "field_of_study", "start_year", "graduation_year", "gpa", "description"]) ||
      typeof e.institution_name !== "string" || e.institution_name.length > 150 ||
      !optionalText(e.degree ?? null, 100) || !optionalText(e.field_of_study ?? null, 150) ||
      !nullableFinite(e.start_year ?? null, -32768, 32767) || !nullableFinite(e.graduation_year ?? null, -32768, 32767) ||
      (typeof e.start_year === "number" && !Number.isInteger(e.start_year)) || (typeof e.graduation_year === "number" && !Number.isInteger(e.graduation_year)) ||
      !nullableFinite(e.gpa ?? null, -999.99, 999.99) || !optionalText(e.description ?? null, 10000)) return false;
    if (typeof e.start_year === "number" && typeof e.graduation_year === "number" && e.graduation_year < e.start_year) return false;
  }
  return true;
}
