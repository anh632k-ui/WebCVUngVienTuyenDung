import Link from "next/link";
import { notFound } from "next/navigation";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { HrJobDetail } from "@/components/jobs/hr-job-detail";
import { requireRole } from "@/lib/auth/session";
import { validJobId } from "@/lib/jobs/contracts";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Chi tiết JD" };
export const dynamic = "force-dynamic";

export default async function HrJobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireRole("HR");
  const { id } = await params;
  if (!validJobId(id)) notFound();
  return <DashboardShell user={user}><Link className="resume-back" href="/hr/jobs">← Danh sách JD</Link><HrJobDetail id={id} /></DashboardShell>;
}
