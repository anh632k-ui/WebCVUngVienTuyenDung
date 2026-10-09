import { NextRequest } from "next/server";
import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validJobCreate, validJobId } from "@/lib/jobs/contracts";
import { hrBackend, hrGate, jobJson } from "@/lib/jobs/gateway";

export async function GET(request: NextRequest) {
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const search = request.nextUrl.searchParams;
  const page = Number(search.get("page") || "1"), limit = Number(search.get("limit") || "10");
  const keyword = (search.get("keyword") || "").trim(), status = search.get("status") || "", parsing = search.get("parsing_status") || "";
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100 ||
    keyword.length > 120 || (status && !["DRAFT", "ACTIVE", "CLOSED"].includes(status)) ||
    (parsing && !["PENDING", "PROCESSING", "PARSED", "FAILED"].includes(parsing))) {
    return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Bộ lọc JD không hợp lệ." } }, 422);
  }
  const q = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (keyword) q.set("keyword", keyword);
  if (status) q.set("status", status);
  if (parsing) q.set("parsing_status", parsing);
  return jobJson(await hrBackend(`/jobs?${q}`, gate.token));
}
export async function POST(request: Request) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const key = request.headers.get("idempotency-key") ?? "";
  if (!validJobId(key)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Thiếu Idempotency-Key UUID." } }, 422);
  const payload = await readJson(request);
  if (!validJobCreate(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Nội dung JD hoặc tổng trọng số chưa hợp lệ." } }, 422);
  return jobJson(await hrBackend("/jobs", gate.token, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(payload) }));
}
