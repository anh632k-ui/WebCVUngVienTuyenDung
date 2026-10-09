import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { ResumeWorkspace } from "@/components/resume/resume-workspace";
import { requireRole } from "@/lib/auth/session";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Hồ sơ CV của tôi" };
export const dynamic = "force-dynamic";

export default async function CandidateResumesPage() {
  const user = await requireRole("CANDIDATE");
  return <DashboardShell user={user}>
    <div className="resume-heading">
      <span className="section-kicker">KHÔNG GIAN ỨNG VIÊN</span>
      <h1>Hồ sơ CV của tôi</h1>
      <p>Tải CV PDF/DOCX, theo dõi phân tích và kiểm tra dữ liệu nhận diện. Chỉ bạn và các quyền quản trị hợp lệ mới có thể truy cập hồ sơ này.</p>
    </div>
    <ResumeWorkspace />
  </DashboardShell>;
}
