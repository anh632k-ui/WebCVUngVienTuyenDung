import { NextRequest } from "next/server";
import { candidateGate, resumeBackend, resumeJson, sameOriginUpload, uploadToBackend } from "@/lib/resume/gateway";
import { isParsingStatus, isResumeFile, isUuid } from "@/lib/resume/contracts";
import { jsonNoStore } from "@/lib/auth/route-utils";

export async function GET(request: NextRequest) {
  const gate = await candidateGate();
  if ("error" in gate) return gate.error;
  const search = request.nextUrl.searchParams;
  const page = Number(search.get("page") || "1");
  const limit = Number(search.get("limit") || "10");
  const keyword = search.get("keyword")?.trim() ?? "";
  const status = search.get("parsing_status") ?? "";
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100 ||
    keyword.length > 120 || (status && !isParsingStatus(status))) {
    return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Bộ lọc CV không hợp lệ." } }, 422);
  }
  const query = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (keyword) query.set("keyword", keyword);
  if (status) query.set("parsing_status", status);
  return resumeJson(await resumeBackend(`/resumes?${query.toString()}`, gate.token));
}

export async function POST(request: Request) {
  if (!sameOriginUpload(request)) return jsonNoStore({ success: false, error: { code: "CSRF_REJECTED", message: "Yêu cầu tải lên không hợp lệ." } }, 403);
  const gate = await candidateGate();
  if ("error" in gate) return gate.error;
  const key = request.headers.get("idempotency-key") ?? "";
  if (!isUuid(key)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Thiếu Idempotency-Key UUID hợp lệ." } }, 422);
  const size = Number(request.headers.get("content-length") ?? "0");
  if (size > 6 * 1024 * 1024) return jsonNoStore({ success: false, error: { code: "FILE_TOO_LARGE", message: "CV tối đa 5 MB." } }, 413);
  let body: FormData;
  try { body = await request.formData(); } catch { return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Không thể đọc file tải lên." } }, 422); }
  const file = body.get("file");
  if (!(file instanceof File) || !isResumeFile(file) || [...body.keys()].some((k) => k !== "file")) {
    return jsonNoStore({ success: false, error: { code: "INVALID_RESUME_FILE", message: "Chỉ nhận PDF/DOCX, dung lượng tối đa 5 MB." } }, 422);
  }
  const outgoing = new FormData();
  outgoing.set("file", file, file.name);
  return resumeJson(await uploadToBackend(outgoing, gate.token, key));
}
