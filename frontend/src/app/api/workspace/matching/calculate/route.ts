import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { validCalculate } from "@/lib/matching/contracts";
import { matchBackend, matchGate, matchJson } from "@/lib/matching/gateway";
export async function POST(request: Request) {
  const denied = rejectUnsafeMutation(request);
  if (denied) return denied;
  const gate = await matchGate();
  if ("error" in gate) return gate.error;
  const payload = await readJson(request);
  if (!validCalculate(payload, gate.role)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Chọn JD và CV hợp lệ, không trùng mã." } }, 422);
  return matchJson(await matchBackend("/matching/calculate", gate.token, { method: "POST", body: JSON.stringify(payload) }));
}
