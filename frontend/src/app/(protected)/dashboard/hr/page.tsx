import { DashboardShell, DashboardWelcome } from "@/components/dashboard/dashboard-shell";
import { requireRole } from "@/lib/auth/session";

export const metadata = { title: "Dashboard nhà tuyển dụng" };

export default async function HrDashboard() {
  const user = await requireRole("HR");
  return <DashboardShell user={user}>
    <DashboardWelcome user={user} title="Tổng quan dành cho nhà tuyển dụng"
      description="Quản lý JD và tiêu chí, đối chiếu CV trong talent pool riêng và xem bảng xếp hạng theo JD." />
  </DashboardShell>;
}
