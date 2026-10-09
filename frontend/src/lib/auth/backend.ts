import "server-only";

import { parseBackendOrigin } from "./backend-config.ts";

const API_PREFIX = "/api/v1";
const REQUEST_TIMEOUT_MS = 8_000;

export type BackendResult = {
  ok: boolean;
  status: number;
  body: unknown;
};

export async function backendRequest(path: string, init: RequestInit = {}): Promise<BackendResult> {
  if (!path.startsWith("/") || path.includes("..")) throw new Error("Invalid backend path");

  try {
    const response = await fetch(`${parseBackendOrigin(process.env.BACKEND_API_URL)}${API_PREFIX}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
    let body: unknown = null;
    try { body = await response.json(); } catch { body = null; }
    return { ok: response.ok, status: response.status, body };
  } catch {
    return {
      ok: false,
      status: 503,
      body: { success: false, error: { code: "BACKEND_UNAVAILABLE", message: "Không thể kết nối máy chủ. Vui lòng thử lại sau." } },
    };
  }
}

export function backendError(result: BackendResult) {
  const body = typeof result.body === "object" && result.body !== null ? result.body as Record<string, unknown> : {};
  const error = typeof body.error === "object" && body.error !== null ? body.error as Record<string, unknown> : {};
  return {
    success: false as const,
    error: {
      code: typeof error.code === "string" ? error.code : "REQUEST_FAILED",
      message: typeof error.message === "string" ? error.message : "Yêu cầu không thành công. Vui lòng thử lại.",
    },
  };
}
