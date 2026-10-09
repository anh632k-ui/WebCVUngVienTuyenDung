import { redirect } from "next/navigation";
import { dashboardForRole } from "@/lib/auth/roles";
import { requireUser } from "@/lib/auth/session";

export default async function DashboardResolver() {
  const user = await requireUser();
  redirect(dashboardForRole(user.role));
}
