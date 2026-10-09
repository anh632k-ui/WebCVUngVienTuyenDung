import type { MatchSummary } from "@/lib/matching/contracts";
export type LeaderboardItem = { rank: number; match: MatchSummary; candidate: { resume_id: string; full_name: string | null; current_title: string | null } };
export type LeaderboardPage = { success: true; data: LeaderboardItem[]; meta: { page: number; limit: number; total_items: number; total_pages: number } };
export function validateLeaderboardQuery(search: URLSearchParams) {
  const page = Number(search.get("page") || "1"), limit = Number(search.get("limit") || "20");
  const rawScore = search.get("min_score");
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) return null;
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (rawScore !== null && rawScore !== "") {
    const value = Number(rawScore);
    if (!Number.isFinite(value) || value < 0 || value > 100 || !/^\d+(?:\.\d{1,3})?$/.test(rawScore)) return null;
    params.set("min_score", String(value));
  }
  return params;
}
