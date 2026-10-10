import { jsonNoStore } from "@/lib/auth/route-utils";
import { hrGate, resumeBackend, resumeJson } from "@/lib/resume/gateway";
import { isUuid } from "@/lib/resume/contracts";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã CV không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  return resumeJson(await resumeBackend(`/resumes/${id}/status`, gate.token));
}
