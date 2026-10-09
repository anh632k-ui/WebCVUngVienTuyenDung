import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validJobId, validWeights } from "@/lib/jobs/contracts";
import { hrBackend, hrGate, jobJson } from "@/lib/jobs/gateway";
export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const { id } = await params;
  if (!validJobId(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã JD không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validWeights(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Tổng ba trọng số phải đúng 1.000; mỗi giá trị có tối đa ba chữ số thập phân." } }, 422);
  return jobJson(await hrBackend(`/jobs/${id}/weights`, gate.token, { method: "PUT", body: JSON.stringify(payload) }));
}
