import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validJobId, validJobStatus } from "@/lib/jobs/contracts";
import { hrBackend, hrGate, jobJson } from "@/lib/jobs/gateway";
export async function PATCH(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const { id } = await params;
  if (!validJobId(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validJobStatus(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Trạng thái không hợp lệ." } }, 422);
  return jobJson(await hrBackend(`/jobs/${id}/status`, gate.token, { method: "PATCH", body: JSON.stringify(payload) }));
}
