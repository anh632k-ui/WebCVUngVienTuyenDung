import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { hrGate, resumeBackend, resumeJson } from "@/lib/resume/gateway";
import { isUuid, validateParsedData } from "@/lib/resume/contracts";

export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const { id } = await params;
  if (!isUuid(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã CV không hợp lệ." } }, 422);
  const gate = await hrGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validateParsedData(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Dữ liệu CV không hợp lệ; hãy kiểm tra các trường và mốc thời gian." } }, 422);
  return resumeJson(await resumeBackend(`/resumes/${id}/parsed-data`, gate.token, { method: "PUT", body: JSON.stringify(payload) }));
}
