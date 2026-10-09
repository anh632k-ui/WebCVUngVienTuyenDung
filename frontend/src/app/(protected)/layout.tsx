import { privateMetadata } from "@/lib/private-metadata";

export const metadata = privateMetadata;
export const dynamic = "force-dynamic";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return children;
}
