import { backendError, backendRequest } from "@/lib/auth/backend";
import { parseUser, validateProfile } from "@/lib/auth/contracts";
import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { expiredSessionCookieOptions, SESSION_COOKIE } from "@/lib/auth/security";
import { readSessionToken } from "@/lib/auth/session";

export async function PUT(request: Request) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const token = await readSessionToken();
  if (!token) return jsonNoStore({ success: false, error: { code: "AUTHENTICATION_REQUIRED", message: "Bạn cần đăng nhập." } }, 401);
  const parsed = validateProfile(await readJson(request));
  if (!parsed.ok) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: parsed.message } }, 422);
  const result = await backendRequest("/users/me", { method: "PUT", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(parsed.value) });
  if (!result.ok) {
    const response = jsonNoStore(backendError(result), result.status);
    if (result.status === 401 || result.status === 403) response.cookies.set(SESSION_COOKIE, "", expiredSessionCookieOptions());
    return response;
  }
  const user = parseUser((result.body as { data?: unknown } | null)?.data);
  if (!user) return jsonNoStore({ success: false, error: { code: "INVALID_BACKEND_RESPONSE", message: "Phản hồi máy chủ không hợp lệ." } }, 502);
  return jsonNoStore({ success: true, data: user });
}
