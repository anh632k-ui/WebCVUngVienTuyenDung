import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validJobId, validJobUpdate } from "@/lib/jobs/contracts";
import { hrBackend, hrGate, jobJson } from "@/lib/jobs/gateway";
type Context = { params: Promise<{ id: string }> };
function invalid() { return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD không hợp lệ." } }, 422); }
export async function GET(_request: Request, { params }: Context) {
  const { id } = await params;
  if (!validJobId(id)) return invalid();
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  return jobJson(await hrBackend(`/jobs/${id}`, gate.token));
}
export async function PUT(request: Request, { params }: Context) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const { id } = await params;
  if (!validJobId(id)) return invalid();
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validJobUpdate(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Dữ liệu cập nhật JD không hợp lệ." } }, 422);
  return jobJson(await hrBackend(`/jobs/${id}`, gate.token, { method: "PUT", body: JSON.stringify(payload) }));
}
export async function DELETE(request: Request, { params }: Context) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const { id } = await params;
  if (!validJobId(id)) return invalid();
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const res = await hrBackend(`/jobs/${id}`, gate.token, { method: "DELETE" });
  return res.ok ? new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } }) : jobJson(res);
}
