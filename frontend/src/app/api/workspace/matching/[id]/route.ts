import { jsonNoStore } from "@/lib/auth/route-utils";
import { isMatchUuid } from "@/lib/matching/contracts";
import { matchBackend, matchGate, matchJson } from "@/lib/matching/gateway";
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isMatchUuid(id)) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Mã matching không hợp lệ." } }, 422);
  const gate = await matchGate();
  if ("error" in gate) return gate.error;
  return matchJson(await matchBackend(`/matching/${id}`, gate.token));
}
