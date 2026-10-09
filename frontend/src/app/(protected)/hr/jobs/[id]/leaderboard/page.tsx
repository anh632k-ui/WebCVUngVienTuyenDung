import Link from "next/link";
import { notFound } from "next/navigation";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { LeaderboardClient } from "@/components/leaderboard/leaderboard-client";
import { requireRole } from "@/lib/auth/session";
import { validJobId } from "@/lib/jobs/contracts";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Bảng xếp hạng CV của JD" };
export const dynamic = "force-dynamic";
export default async function LeaderboardPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireRole("HR");
  const { id } = await params;
  if (!validJobId(id)) notFound();
  return <DashboardShell user={user}>
    <Link className="resume-back" href={`/hr/jobs/${id}`}>← Quay về JD</Link>
    <header className="job-heading"><span className="section-kicker">TALENT POOL HR</span><h1>Bảng xếp hạng CV</h1>
      <p>Chỉ liệt kê kết quả COMPLETED hiện hành của JD và CV trong talent pool mà HR sở hữu. Điểm số hỗ trợ tham khảo, không quyết định tuyển dụng.</p></header>
    <LeaderboardClient jobId={id} />
  </DashboardShell>;
}
