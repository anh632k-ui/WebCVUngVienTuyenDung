import "server-only";
import { readSessionToken, verifySession } from "@/lib/auth/session";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { backendRequest, backendError } from "@/lib/auth/backend";
import type { WorkspaceRole } from "./contracts";

export async function matchGate() {
  const session = await verifySession();
  if (!session.user) {
    const status = session.status === 401 ? 401 : session.status === 403 ? 403 : 503;
    return { error: jsonNoStore({ success: false, error: { code: "SESSION_UNAVAILABLE", message: "Không thể xác minh phiên." } }, status) } as const;
  }
  const role = session.user.role;
  if (role !== "CANDIDATE" && role !== "HR") return { error: jsonNoStore({ success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Chức năng chỉ dành cho ứng viên/nhà tuyển dụng." } }, 403) } as const;
  const token = await readSessionToken();
  if (!token) return { error: jsonNoStore({ success: false, error: { code: "AUTHENTICATION_REQUIRED", message: "Bạn cần đăng nhập." } }, 401) } as const;
  return { token, role: role as WorkspaceRole } as const;
}
export async function matchBackend(path: string, token: string, init: RequestInit = {}) {
  return backendRequest(path, { ...init, headers: { Authorization: `Bearer ${token}`, ...init.headers } });
}
export function matchJson(result: { ok: boolean; status: number; body: unknown }) {
  return result.ok ? jsonNoStore(result.body, result.status) : jsonNoStore(backendError(result), result.status);
}
export function pagination(params: URLSearchParams, limitDefault = 20) {
  const page = Number(params.get("page") || "1"), limit = Number(params.get("limit") || String(limitDefault));
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) return null;
  const q = new URLSearchParams({ page: String(page), limit: String(limit) });
  return q;
}
