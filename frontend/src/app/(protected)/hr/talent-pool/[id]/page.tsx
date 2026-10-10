import Link from "next/link";
import { notFound } from "next/navigation";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { ResumeDetailClient } from "@/components/resume/resume-detail";
import { requireRole } from "@/lib/auth/session";
import { isUuid } from "@/lib/resume/contracts";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Chi tiết CV talent pool" };
export const dynamic = "force-dynamic";

export default async function HrTalentPoolDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireRole("HR");
  const { id } = await params;
  if (!isUuid(id)) notFound();
  return <DashboardShell user={user}>
    <Link className="resume-back" href="/hr/talent-pool">← Kho CV của tôi</Link>
    <ResumeDetailClient id={id} area="hr" />
  </DashboardShell>;
}
