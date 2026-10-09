import { redirect } from "next/navigation";
import { AuthShell } from "@/components/auth/auth-shell";
import { LoginForm } from "@/components/auth/auth-forms";
import { dashboardForRole } from "@/lib/auth/roles";
import { verifySession } from "@/lib/auth/session";

export const metadata = { title: "Đăng nhập" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const session = await verifySession();
  if (session.user) redirect(dashboardForRole(session.user.role));
  const query = await searchParams;
  const successMessage = query.registered === "1" ? "Đăng ký thành công. Hãy đăng nhập để tiếp tục." : query.password_changed === "1" ? "Đổi mật khẩu thành công. Hãy đăng nhập lại." : undefined;
  return <AuthShell eyebrow="CHÀO MỪNG TRỞ LẠI" title="Đăng nhập an toàn vào CVInsight." description="Phiên của bạn được xác minh trực tiếp với backend trước khi truy cập khu vực tài khoản." alternate={{ text: "Chưa có tài khoản?", label: "Đăng ký", href: "/dang-ky" }}><LoginForm sessionExpired={query.reason === "expired"} successMessage={successMessage} /></AuthShell>;
}
