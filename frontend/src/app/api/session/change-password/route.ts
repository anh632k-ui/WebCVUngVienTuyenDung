import { backendError, backendRequest } from "@/lib/auth/backend";
import { validatePasswordChange } from "@/lib/auth/contracts";
import { jsonNoStore, readJson, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { expiredSessionCookieOptions, SESSION_COOKIE } from "@/lib/auth/security";
import { readSessionToken } from "@/lib/auth/session";

export async function PUT(request: Request) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const token = await readSessionToken();
  if (!token) return jsonNoStore({ success: false, error: { code: "AUTHENTICATION_REQUIRED", message: "Bạn cần đăng nhập." } }, 401);
  const parsed = validatePasswordChange(await readJson(request));
  if (!parsed.ok) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: parsed.message } }, 422);
  const result = await backendRequest("/auth/change-password", { method: "PUT", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(parsed.value) });
  if (!result.ok) {
    const response = jsonNoStore(backendError(result), result.status);
    if (result.status === 401 || result.status === 403) response.cookies.set(SESSION_COOKIE, "", expiredSessionCookieOptions());
    return response;
  }
  const response = jsonNoStore({ success: true, data: { message: "Đổi mật khẩu thành công. Vui lòng đăng nhập lại.", session_cleared: true } });
  response.cookies.set(SESSION_COOKIE, "", expiredSessionCookieOptions());
  return response;
}
