import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validAdminPatch, validAdminUserId } from "@/lib/admin/contracts";
import { adminBackend, adminGate, adminJson } from "@/lib/admin/gateway";
export async function PATCH(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const { id } = await params;
  if (!validAdminUserId(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã người dùng không hợp lệ." } }, 422);
  const gate = await adminGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validAdminPatch(payload)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Chỉ được thay đổi is_active hoặc role Candidate/HR." } }, 422);
  return adminJson(await adminBackend(`/admin/users/${id}`, gate.token, { method: "PATCH", body: JSON.stringify(payload) }));
}
