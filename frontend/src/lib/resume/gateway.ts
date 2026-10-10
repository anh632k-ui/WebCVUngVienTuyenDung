import "server-only";
import { backendError, backendRequest, type BackendResult } from "@/lib/auth/backend";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { readSessionToken, verifySession } from "@/lib/auth/session";
import { parseBackendOrigin } from "@/lib/auth/backend-config";

export async function candidateGate() {
  const session = await verifySession();
  if (!session.user) {
    const status = session.status === 401 ? 401 : session.status === 403 ? 403 : 503;
    return { error: jsonNoStore({ success: false, error: {
      code: status === 401 ? "AUTHENTICATION_REQUIRED" : status === 403 ? "ACCOUNT_INACTIVE" : "SESSION_UNAVAILABLE",
      message: status === 401 ? "Vui lòng đăng nhập." : status === 403 ? "Tài khoản không hoạt động." : "Chưa thể xác minh phiên. Vui lòng thử lại.",
    } }, status) } as const;
  }
  if (session.user.role !== "CANDIDATE") {
    return { error: jsonNoStore({ success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Chỉ tài khoản Ứng viên được sử dụng chức năng này." } }, 403) } as const;
  }
  const token = await readSessionToken();
  if (!token) return { error: jsonNoStore({ success: false, error: { code: "AUTHENTICATION_REQUIRED", message: "Vui lòng đăng nhập." } }, 401) } as const;
  return { token } as const;
}

/** HR talent-pool guard; FastAPI still enforces owner_user_id per resource. */
export async function hrGate() {
  const session = await verifySession();
  if (!session.user) {
    const status = session.status === 401 ? 401 : session.status === 403 ? 403 : 503;
    return { error: jsonNoStore({ success: false, error: {
      code: status === 401 ? "AUTHENTICATION_REQUIRED" : status === 403 ? "ACCOUNT_INACTIVE" : "SESSION_UNAVAILABLE",
      message: status === 401 ? "Vui lòng đăng nhập." : status === 403 ? "Tài khoản không hoạt động." : "Chưa thể xác minh phiên. Vui lòng thử lại.",
    } }, status) } as const;
  }
  if (session.user.role !== "HR") {
    return { error: jsonNoStore({ success: false, error: { code: "INSUFFICIENT_PERMISSIONS", message: "Chỉ Nhà tuyển dụng được quản lý talent pool." } }, 403) } as const;
  }
  const token = await readSessionToken();
  if (!token) return { error: jsonNoStore({ success: false, error: { code: "AUTHENTICATION_REQUIRED", message: "Vui lòng đăng nhập." } }, 401) } as const;
  return { token } as const;
}

export function resumeJson(result: BackendResult) {
  return result.ok ? jsonNoStore(result.body, result.status) : jsonNoStore(backendError(result), result.status);
}

export async function resumeBackend(path: string, token: string, init: RequestInit = {}) {
  return backendRequest(path, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...init.headers },
  });
}

export function sameOriginUpload(request: Request) {
  const origin = request.headers.get("origin");
  const site = request.headers.get("sec-fetch-site");
  const type = request.headers.get("content-type") ?? "";
  try {
    if (!origin || new URL(origin).origin !== new URL(request.url).origin ||
      (site && site !== "same-origin") || !/^multipart\/form-data;\s*boundary=/i.test(type)) return false;
    return true;
  } catch { return false; }
}

export async function uploadToBackend(form: FormData, token: string, key: string): Promise<BackendResult> {
  try {
    const response = await fetch(`${parseBackendOrigin(process.env.BACKEND_API_URL)}/api/v1/resumes/upload`, {
      method: "POST", body: form, cache: "no-store", signal: AbortSignal.timeout(20_000),
      headers: { Authorization: `Bearer ${token}`, "Idempotency-Key": key, Accept: "application/json" },
    });
    const body: unknown = await response.json().catch(() => null);
    return { ok: response.ok, status: response.status, body };
  } catch {
    return { ok: false, status: 503, body: { success: false, error: {
      code: "BACKEND_UNAVAILABLE", message: "Không thể tải CV lúc này. Có thể thử lại với cùng yêu cầu.",
    } } };
  }
}
