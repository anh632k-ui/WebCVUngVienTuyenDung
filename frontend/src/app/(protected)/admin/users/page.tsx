import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { AdminUsersWorkspace } from "@/components/admin/admin-users-workspace";
import { requireRole } from "@/lib/auth/session";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Quản lý tài khoản – Admin" };
export const dynamic = "force-dynamic";

export default async function AdminUsersPage() {
  const user = await requireRole("ADMIN");
  return <DashboardShell user={user}>
    <header className="job-heading"><span className="section-kicker">QUẢN TRỊ HỆ THỐNG</span><h1>Quản lý tài khoản</h1>
      <p>Xem và lọc tài khoản theo vai trò/trạng thái. Thay đổi quyền hoặc trạng thái chỉ khi được backend chấp thuận; không có quyền đọc CV riêng tư ngoài canonical.</p></header>
    <AdminUsersWorkspace currentUserId={user.id} />
  </DashboardShell>;
}
