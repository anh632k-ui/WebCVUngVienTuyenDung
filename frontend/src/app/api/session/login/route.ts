import { backendError, backendRequest } from "@/lib/auth/backend";
import { parseUser, validateLogin } from "@/lib/auth/contracts";
import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/security";

export async function POST(request: Request) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const parsed = validateLogin(await readJson(request));
  if (!parsed.ok) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: parsed.message } }, 422);
  const result = await backendRequest("/auth/login", { method: "POST", body: JSON.stringify(parsed.value) });
  if (!result.ok) return jsonNoStore(backendError(result), result.status);

  const envelope = result.body as { data?: Record<string, unknown> } | null;
  const data = envelope?.data;
  const user = parseUser(data?.user);
  const token = data?.access_token;
  const expiresIn = data?.expires_in;
  if (!user || typeof token !== "string" || token.length === 0 || typeof expiresIn !== "number" || !Number.isFinite(expiresIn) || expiresIn <= 0) {
    return jsonNoStore({ success: false, error: { code: "INVALID_BACKEND_RESPONSE", message: "Phản hồi đăng nhập không hợp lệ." } }, 502);
  }

  const response = jsonNoStore({ success: true, data: { user, expires_in: Math.floor(expiresIn) } });
  response.cookies.set(SESSION_COOKIE, token, sessionCookieOptions(expiresIn));
  return response;
}
