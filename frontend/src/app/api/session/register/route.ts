import { backendError, backendRequest } from "@/lib/auth/backend";
import { parseUser, validateRegistration } from "@/lib/auth/contracts";
import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";

export async function POST(request: Request) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const parsed = validateRegistration(await readJson(request));
  if (!parsed.ok) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: parsed.message } }, 422);
  const result = await backendRequest("/auth/register", { method: "POST", body: JSON.stringify(parsed.value) });
  if (!result.ok) return jsonNoStore(backendError(result), result.status);
  const envelope = result.body as { data?: unknown } | null;
  const user = parseUser(envelope?.data);
  if (!user) return jsonNoStore({ success: false, error: { code: "INVALID_BACKEND_RESPONSE", message: "Phản hồi máy chủ không hợp lệ." } }, 502);
  return jsonNoStore({ success: true, data: user }, 201);
}
