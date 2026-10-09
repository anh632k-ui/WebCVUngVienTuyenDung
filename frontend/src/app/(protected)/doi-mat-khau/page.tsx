import { ChangePasswordForm } from "@/components/dashboard/change-password-form";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { requireUser } from "@/lib/auth/session";

export const metadata = { title: "Đổi mật khẩu" };

export default async function ChangePasswordPage() {
  const user = await requireUser();
  return <DashboardShell user={user}><section className="settings-heading"><span className="section-kicker">BẢO MẬT TÀI KHOẢN</span><h1>Đổi mật khẩu</h1><p>Xác nhận mật khẩu hiện tại và chọn mật khẩu mới có ít nhất 8 ký tự.</p></section><section className="settings-card single"><ChangePasswordForm /></section></DashboardShell>;
}
