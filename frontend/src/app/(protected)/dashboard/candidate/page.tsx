import { DashboardShell, DashboardWelcome } from "@/components/dashboard/dashboard-shell";
import { requireRole } from "@/lib/auth/session";

export const metadata = { title: "Dashboard ứng viên" };

export default async function CandidateDashboard() {
  const user = await requireRole("CANDIDATE");
  return <DashboardShell user={user}>
    <DashboardWelcome user={user} title="Tổng quan dành cho ứng viên"
      description="Quản lý CV của bạn, kiểm tra dữ liệu được trích xuất và thực hiện self-matching riêng tư với JD đang mở." />
  </DashboardShell>;
}
