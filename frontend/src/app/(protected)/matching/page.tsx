import { redirect } from "next/navigation";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { MatchWorkspace } from "@/components/matching/match-workspace";
import { requireUser } from "@/lib/auth/session";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Đối chiếu CV và JD" };
export const dynamic = "force-dynamic";

export default async function MatchingPage() {
  const user = await requireUser();
  if (user.role !== "CANDIDATE" && user.role !== "HR") redirect("/dashboard/admin");
  return <DashboardShell user={user}>
    <header className="job-heading"><span className="section-kicker">HYBRID MATCHING V1</span><h1>Đối chiếu CV và JD</h1>
      <p>{user.role === "CANDIDATE" ? "Tự đối chiếu CV của bạn với JD đang mở. Kết quả self-match không tự động chia sẻ với nhà tuyển dụng." :
        "Đối chiếu một JD với các CV trong talent pool thuộc quyền sở hữu HR. Backend xác minh ownership và trạng thái trước khi chạy batch."}</p>
    </header>
    <MatchWorkspace role={user.role} />
  </DashboardShell>;
}
