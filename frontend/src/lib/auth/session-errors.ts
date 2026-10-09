export type SessionFailure = "unauthenticated" | "inactive" | "unavailable" | "upstream";

export function classifySessionFailure(status: number): SessionFailure {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "inactive";
  if (status === 503) return "unavailable";
  return "upstream";
}

export function sessionFailurePath(status: number) {
  const failure = classifySessionFailure(status);
  return failure === "unauthenticated" ? "/dang-nhap?reason=expired" : `/trang-thai-phien?reason=${failure}`;
}
