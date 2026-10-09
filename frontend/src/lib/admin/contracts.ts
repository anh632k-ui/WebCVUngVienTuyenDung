import type { CurrentUser, UserRole } from "@/lib/auth/types";
export type AdminUser = CurrentUser;
export type AdminPage = { success: true; data: AdminUser[]; meta: { page: number; limit: number; total_items: number; total_pages: number } };
export type AdminUserResponse = { success: true; data: AdminUser };
const uuid = /^[\da-f]{8}-[\da-f]{4}-[1-8][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
export function validAdminUserId(value: string) { return uuid.test(value); }
export function validAdminPatch(input: unknown) {
  if (input === null || typeof input !== "object" || Array.isArray(input)) return false;
  const v = input as Record<string, unknown>;
  const keys = Object.keys(v);
  if (!keys.length || keys.some((key) => key !== "is_active" && key !== "role")) return false;
  if ("is_active" in v && typeof v.is_active !== "boolean") return false;
  if ("role" in v && v.role !== "CANDIDATE" && v.role !== "HR") return false;
  return true;
}
export function adminUserQuery(search: URLSearchParams) {
  const page = Number(search.get("page") || "1"), limit = Number(search.get("limit") || "20");
  const role = search.get("role") || "", active = search.get("is_active") || "", keyword = (search.get("keyword") || "").trim();
  const roles: UserRole[] = ["CANDIDATE", "HR", "ADMIN"];
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100 ||
    (role && !roles.includes(role as UserRole)) || (active && active !== "true" && active !== "false") || keyword.length > 120) return null;
  const q = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (role) q.set("role", role);
  if (active) q.set("is_active", active);
  if (keyword) q.set("keyword", keyword);
  return q;
}
