import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { ProfileForm } from "@/components/dashboard/profile-form";
import { requireUser } from "@/lib/auth/session";

export const metadata = { title: "Tài khoản" };
const roleNames = { CANDIDATE: "Ứng viên", HR: "Nhà tuyển dụng", ADMIN: "Quản trị viên" } as const;

export default async function AccountPage() {
  const user = await requireUser();
  return <DashboardShell user={user}><section className="settings-heading"><span className="section-kicker">HỒ SƠ TÀI KHOẢN</span><h1>Thông tin cá nhân</h1><p>Email, vai trò và trạng thái do hệ thống quản lý; bạn chỉ có thể cập nhật họ tên và số điện thoại.</p></section><div className="settings-grid"><section className="settings-card"><h2>Thông tin chỉ đọc</h2><dl><div><dt>Email</dt><dd>{user.email}</dd></div><div><dt>Vai trò</dt><dd>{roleNames[user.role]}</dd></div><div><dt>Trạng thái</dt><dd>{user.is_active ? "Đang hoạt động" : "Đã vô hiệu hóa"}</dd></div></dl></section><section className="settings-card"><h2>Cập nhật hồ sơ</h2><ProfileForm user={user} /></section></div></DashboardShell>;
}
