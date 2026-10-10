import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { ResumeWorkspace } from "@/components/resume/resume-workspace";
import { requireRole } from "@/lib/auth/session";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Talent pool CV – Nhà tuyển dụng" };
export const dynamic = "force-dynamic";

export default async function HrTalentPoolPage() {
  const user = await requireRole("HR");
  return <DashboardShell user={user}>
    <header className="resume-heading">
      <span className="section-kicker">KHO CV NHÀ TUYỂN DỤNG</span>
      <h1>Talent pool của tôi</h1>
      <p>Quản lý CV do chính tài khoản HR tải lên để đối chiếu với JD thuộc quyền sở hữu của bạn. CV Candidate không tự động xuất hiện trong kho HR.</p>
    </header>
    <ResumeWorkspace area="hr" />
  </DashboardShell>;
}
