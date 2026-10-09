export const SESSION_COOKIE = "cvinsight_session";

export function sessionCookieOptions(expiresIn: number, production = process.env.NODE_ENV === "production") {
  return {
    httpOnly: true,
    secure: production,
    sameSite: "lax" as const,
    path: "/",
    maxAge: Math.max(1, Math.floor(expiresIn)),
    priority: "high" as const,
  };
}

export function expiredSessionCookieOptions(production = process.env.NODE_ENV === "production") {
  return { ...sessionCookieOptions(1, production), maxAge: 0 };
}

export function validateMutationRequest(request: Request): string | null {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (contentType !== "application/json") return "Yêu cầu phải sử dụng application/json.";

  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin") return "Yêu cầu khác nguồn đã bị từ chối.";

  const origin = request.headers.get("origin");
  if (!origin) return "Thiếu thông tin nguồn của yêu cầu.";
  try {
    if (new URL(origin).origin !== new URL(request.url).origin) return "Yêu cầu khác nguồn đã bị từ chối.";
  } catch {
    return "Nguồn yêu cầu không hợp lệ.";
  }
  return null;
}
