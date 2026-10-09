import type { UserRole } from "./types.ts";

export function dashboardForRole(role: UserRole) {
  return role === "CANDIDATE"
    ? "/dashboard/candidate"
    : role === "HR"
      ? "/dashboard/hr"
      : "/dashboard/admin";
}
