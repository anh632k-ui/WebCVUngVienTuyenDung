import "server-only";
import { backendRequest, backendError } from "@/lib/auth/backend";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { readSessionToken, verifySession } from "@/lib/auth/session";

export async function adminGate() {
  const session = await verifySession();
  if (!session.user) {
    const status = session.status === 401 ? 401 : session.status === 403 ? 403 : 503;
    return { error: jsonNoStore({ success: false, error: { code: "SESSION_UNAVAILABLE", message: "Không thể xác minh phiên quản trị." } }, status) } as const;
  }
  if (session.user.role !== "ADMIN") return { error: jsonNoStore({ success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Không có quyền quản trị." } }, 403) } as const;
  const token = await readSessionToken();
  if (!token) return { error: jsonNoStore({ success: false, error: { code: "AUTHENTICATION_REQUIRED", message: "Vui lòng đăng nhập." } }, 401) } as const;
  return { token } as const;
}
export async function adminBackend(path: string, token: string, init: RequestInit = {}) {
  return backendRequest(path, { ...init, headers: { Authorization: `Bearer ${token}`, ...init.headers } });
}
export function adminJson(result: { ok: boolean; status: number; body: unknown }) {
  return result.ok ? jsonNoStore(result.body, result.status) : jsonNoStore(backendError(result), result.status);
}
