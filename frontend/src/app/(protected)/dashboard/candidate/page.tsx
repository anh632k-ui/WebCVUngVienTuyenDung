import { DashboardShell, DashboardWelcome } from "@/components/dashboard/dashboard-shell";
import Link from "next/link";
import { requireRole } from "@/lib/auth/session";

export const metadata = { title: "Dashboard ứng viên" };

export default async function CandidateDashboard() {
  const user = await requireRole("CANDIDATE");
  return <DashboardShell user={user}><DashboardWelcome user={user} title="Tổng quan dành cho ứng viên" description="Nền tảng tài khoản đã sẵn sàng cho các luồng CV và self-matching ở giai đoạn tiếp theo." upcoming={["Quản lý và phân tích CV", "Đối chiếu CV với JD", "Theo dõi Skill Gap"]} /><Link className="button button-primary" href="/cv">Quản lý CV của tôi →</Link></DashboardShell>;
}
