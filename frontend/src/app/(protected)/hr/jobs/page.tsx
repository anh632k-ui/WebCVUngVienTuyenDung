import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { HrJobsWorkspace } from "@/components/jobs/hr-jobs-workspace";
import { requireRole } from "@/lib/auth/session";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Quản lý JD – Nhà tuyển dụng" };
export const dynamic = "force-dynamic";

export default async function HrJobsPage() {
  const user = await requireRole("HR");
  return <DashboardShell user={user}>
    <header className="job-heading"><span className="section-kicker">KHÔNG GIAN NHÀ TUYỂN DỤNG</span><h1>Mô tả công việc (JD)</h1>
      <p>Tạo JD, theo dõi phân tích, xác nhận tiêu chí kỹ năng và điều chỉnh trọng số. Tất cả JD trong danh sách đều thuộc quyền sở hữu tài khoản HR.</p></header>
    <HrJobsWorkspace />
  </DashboardShell>;
}
