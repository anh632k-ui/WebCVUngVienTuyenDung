import { DashboardShell, DashboardWelcome } from "@/components/dashboard/dashboard-shell";
import { requireRole } from "@/lib/auth/session";

export const metadata = { title: "Dashboard quản trị" };

export default async function AdminDashboard() {
  const user = await requireRole("ADMIN");
  return <DashboardShell user={user}>
    <DashboardWelcome user={user} title="Tổng quan quản trị hệ thống"
      description="Theo dõi tài khoản và quản lý trạng thái hoặc vai trò được phép theo chính sách của hệ thống." />
  </DashboardShell>;
}
