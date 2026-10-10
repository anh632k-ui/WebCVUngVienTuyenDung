import { jsonNoStore } from "@/lib/auth/route-utils";
import { backendError } from "@/lib/auth/backend";
import { parseBackendOrigin } from "@/lib/auth/backend-config";
import { hrGate } from "@/lib/resume/gateway";
import { isUuid } from "@/lib/resume/contracts";

const TYPES = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã CV không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  try {
    const response = await fetch(`${parseBackendOrigin(process.env.BACKEND_API_URL)}/api/v1/resumes/${id}/download`, {
      headers: { Authorization: `Bearer ${gate.token}`, Accept: "application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
      cache: "no-store", signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) {
      let body: unknown;
      try { body = await response.json(); } catch { body = null; }
      return jsonNoStore(backendError({ ok: false, status: response.status, body }), response.status);
    }
    const type = response.headers.get("content-type")?.split(";")[0]?.trim() ?? "";
    if (!TYPES.has(type)) return jsonNoStore({ success: false, error: { code: "INVALID_BACKEND_RESPONSE", message: "Định dạng file phản hồi không hợp lệ." } }, 502);
    const headers = new Headers({ "Content-Type": type, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" });
    const disposition = response.headers.get("content-disposition");
    if (disposition) headers.set("Content-Disposition", disposition);
    return new Response(response.body, { status: 200, headers });
  } catch {
    return jsonNoStore({ success: false, error: { code: "BACKEND_UNAVAILABLE", message: "Không thể tải file. Vui lòng thử lại sau." } }, 503);
  }
}
