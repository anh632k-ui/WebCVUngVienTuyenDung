import { NextRequest } from "next/server";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { matchBackend, matchGate, matchJson, pagination } from "@/lib/matching/gateway";
export async function GET(request: NextRequest) {
  const gate = await matchGate();
  if ("error" in gate) return gate.error;
  const q = pagination(request.nextUrl.searchParams, 100);
  if (!q) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Phân trang không hợp lệ." } }, 422);
  if (gate.role === "CANDIDATE") q.set("status", "ACTIVE");
  return matchJson(await matchBackend(`/jobs?${q}`, gate.token));
}
