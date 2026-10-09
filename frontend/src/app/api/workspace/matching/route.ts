import { NextRequest } from "next/server";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { isMatchUuid } from "@/lib/matching/contracts";
import { matchBackend, matchGate, matchJson, pagination } from "@/lib/matching/gateway";
export async function GET(request: NextRequest) {
  const gate = await matchGate();
  if ("error" in gate) return gate.error;
  const q = pagination(request.nextUrl.searchParams);
  if (!q) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Phân trang không hợp lệ." } }, 422);
  for (const key of ["job_id", "resume_id"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value && !isMatchUuid(value)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD/CV không hợp lệ." } }, 422);
    if (value) q.set(key, value);
  }
  const status = request.nextUrl.searchParams.get("status") || "";
  if (status && !["PENDING", "PROCESSING", "COMPLETED", "FAILED"].includes(status)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Trạng thái matching không hợp lệ." } }, 422);
  if (status) q.set("status", status);
  return matchJson(await matchBackend(`/matching?${q}`, gate.token));
}
