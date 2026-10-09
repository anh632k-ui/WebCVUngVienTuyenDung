import Link from "next/link";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { ResumeDetailClient } from "@/components/resume/resume-detail";
import { requireRole } from "@/lib/auth/session";
import { isUuid } from "@/lib/resume/contracts";
import { notFound } from "next/navigation";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Chi tiết CV" };
export const dynamic = "force-dynamic";

export default async function CandidateResumeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireRole("CANDIDATE");
  const { id } = await params;
  if (!isUuid(id)) notFound();
  return <DashboardShell user={user}>
    <Link className="resume-back" href="/cv">← Danh sách CV</Link>
    <ResumeDetailClient id={id} />
  </DashboardShell>;
}
