import { DashboardShell, DashboardWelcome } from "@/components/dashboard/dashboard-shell";
import { requireRole } from "@/lib/auth/session";

export const metadata = { title: "Dashboard quản trị" };

export default async function AdminDashboard() {
  const user = await requireRole("ADMIN");
  return <DashboardShell user={user}><DashboardWelcome user={user} title="Tổng quan quản trị hệ thống" description="Admin có shell giám sát riêng; F02 chưa triển khai thao tác quản lý người dùng hoặc truy cập dữ liệu nghiệp vụ riêng tư." upcoming={["Quản lý trạng thái tài khoản", "Kiểm soát chuyển đổi vai trò", "Giám sát tài nguyên theo chính sách"]} /></DashboardShell>;
}
