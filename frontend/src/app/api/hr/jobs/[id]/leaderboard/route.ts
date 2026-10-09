import { NextRequest } from "next/server";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { validJobId } from "@/lib/jobs/contracts";
import { hrBackend, hrGate, jobJson } from "@/lib/jobs/gateway";
import { validateLeaderboardQuery } from "@/lib/leaderboard/contracts";
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!validJobId(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const query = validateLeaderboardQuery(request.nextUrl.searchParams);
  if (!query) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Điểm tối thiểu hoặc phân trang không hợp lệ." } }, 422);
  return jobJson(await hrBackend(`/jobs/${id}/leaderboard?${query}`, gate.token));
}
