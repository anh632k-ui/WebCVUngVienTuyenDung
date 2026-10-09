export type MatchStatus = "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
export type WorkspaceRole = "CANDIDATE" | "HR";
export type MatchSummary = {
  id: string; job_id: string; resume_id: string; generation: number; resume_revision: number; job_revision: number;
  status: MatchStatus; overall_score: number | null; skill_score: number | null;
  semantic_score: number | null; experience_score: number | null; algorithm_version: string;
  embedding_model: string | null; embedding_preprocessing_version: string | null;
  error_message: string | null; created_at: string; updated_at: string; calculated_at: string | null;
};
export type MatchDetail = MatchSummary & {
  matched_skills: Record<string, unknown>[]; missing_skills: Record<string, unknown>[]; gap_analysis_summary: string | null;
};
export type GapAnalysis = { match_id: string; overall_score: number; skill_score: number | null; semantic_score: number | null; experience_score: number | null; matched_skills: Record<string, unknown>[]; missing_skills: Record<string, unknown>[]; recommendation: string | null; explanation: string | null };
export type MatchPage = { success: true; data: MatchSummary[]; meta: { page: number; limit: number; total_items: number; total_pages: number } };
export type MatchResponse = { success: true; data: MatchDetail };
export type GapResponse = { success: true; data: GapAnalysis };
export type TriggerResponse = { success: true; data: { job_id: string; match_ids: string[]; total_matches: number; status: "PENDING" } };
const uuid = /^[\da-f]{8}-[\da-f]{4}-[1-8][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
export function isMatchUuid(value: string) { return uuid.test(value); }
export function validCalculate(value: unknown, role: WorkspaceRole) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  if (Object.keys(data).some((key) => !["job_id", "resume_ids"].includes(key))) return false;
  if (typeof data.job_id !== "string" || !isMatchUuid(data.job_id) || !Array.isArray(data.resume_ids) ||
    data.resume_ids.length === 0 || data.resume_ids.length > (role === "CANDIDATE" ? 1 : 100)) return false;
  return data.resume_ids.every((id) => typeof id === "string" && isMatchUuid(id)) && new Set(data.resume_ids).size === data.resume_ids.length;
}
export function safeScore(value: number | null) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}
export function skillLabel(value: Record<string, unknown>) {
  for (const key of ["skill_name", "name", "canonical_name", "skill_id"]) {
    const field = value[key];
    if (typeof field === "string" && field.length) return field;
    if (key === "skill_id" && typeof field === "number" && Number.isSafeInteger(field)) return `Skill #${field}`;
  }
  return "Kỹ năng chưa định danh";
}
