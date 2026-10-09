import { privateMetadata } from "@/lib/private-metadata";

export const metadata = privateMetadata;

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return children;
}
