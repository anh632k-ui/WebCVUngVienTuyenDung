import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validCriteria, validJobId } from "@/lib/jobs/contracts";
import { hrBackend, hrGate, jobJson } from "@/lib/jobs/gateway";
type Context = { params: Promise<{ id: string }> };
export async function GET(_request: Request, { params }: Context) {
  const { id } = await params;
  if (!validJobId(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  return jobJson(await hrBackend(`/jobs/${id}/criteria`, gate.token));
}
export async function PUT(request: Request, { params }: Context) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const { id } = await params;
  if (!validJobId(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validCriteria(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Criteria yêu cầu tối thiểu một kỹ năng taxonomy hợp lệ và thời gian đúng định dạng." } }, 422);
  return jobJson(await hrBackend(`/jobs/${id}/criteria`, gate.token, { method: "PUT", body: JSON.stringify(payload) }));
}
