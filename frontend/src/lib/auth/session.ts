import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { backendRequest } from "./backend";
import { parseUser } from "./contracts.ts";
import { dashboardForRole } from "./roles.ts";
import { SESSION_COOKIE } from "./security.ts";
import type { CurrentUser, UserRole } from "./types.ts";

export async function readSessionToken() {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function verifySession(): Promise<{ user: CurrentUser | null; status: number }> {
  const token = await readSessionToken();
  if (!token) return { user: null, status: 401 };
  const result = await backendRequest("/users/me", { headers: { Authorization: `Bearer ${token}` } });
  if (!result.ok) return { user: null, status: result.status };
  const envelope = typeof result.body === "object" && result.body !== null ? result.body as Record<string, unknown> : null;
  const user = parseUser(envelope?.data);
  return user?.is_active ? { user, status: 200 } : { user: null, status: user ? 403 : 502 };
}

export async function requireUser() {
  const session = await verifySession();
  if (!session.user) redirect("/dang-nhap?reason=expired");
  return session.user;
}

export async function requireRole(role: UserRole) {
  const user = await requireUser();
  if (user.role !== role) redirect(dashboardForRole(user.role));
  return user;
}
