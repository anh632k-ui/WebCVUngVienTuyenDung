import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { MatchDetailClient } from "@/components/matching/match-detail";
import { requireUser } from "@/lib/auth/session";
import { isMatchUuid } from "@/lib/matching/contracts";
import { privateMetadata } from "@/lib/private-metadata";

export const metadata = { ...privateMetadata, title: "Chi tiết đối chiếu CV/JD" };
export const dynamic = "force-dynamic";

export default async function MatchDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  if (user.role !== "CANDIDATE" && user.role !== "HR") redirect("/dashboard/admin");
  const { id } = await params;
  if (!isMatchUuid(id)) notFound();
  return <DashboardShell user={user}><Link className="resume-back" href="/matching">← Danh sách matching</Link><MatchDetailClient id={id} /></DashboardShell>;
}
