import { DashboardShell, DashboardWelcome } from "@/components/dashboard/dashboard-shell";
import { requireRole } from "@/lib/auth/session";

export const metadata = { title: "Dashboard nhà tuyển dụng" };

export default async function HrDashboard() {
  const user = await requireRole("HR");
  return <DashboardShell user={user}><DashboardWelcome user={user} title="Tổng quan dành cho nhà tuyển dụng" description="Shell theo vai trò đã sẵn sàng; JD, talent pool và batch matching sẽ được triển khai ở các feature sau." upcoming={["Quản lý JD và tiêu chí", "Quản lý talent pool", "Batch matching và leaderboard"]} /></DashboardShell>;
}
