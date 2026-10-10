import { jsonNoStore, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { hrGate, resumeBackend, resumeJson } from "@/lib/resume/gateway";
import { isUuid } from "@/lib/resume/contracts";

type Context = { params: Promise<{ id: string }> };
export async function GET(_request: Request, { params }: Context) {
  const { id } = await params;
  if (!isUuid(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã CV không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  return resumeJson(await resumeBackend(`/resumes/${id}`, gate.token));
}
export async function DELETE(request: Request, { params }: Context) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const { id } = await params;
  if (!isUuid(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã CV không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const result = await resumeBackend(`/resumes/${id}`, gate.token, { method: "DELETE" });
  return result.ok ? new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } }) : resumeJson(result);
}
