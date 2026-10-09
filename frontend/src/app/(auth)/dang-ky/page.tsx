import { redirect } from "next/navigation";
import { AuthShell } from "@/components/auth/auth-shell";
import { RegisterForm } from "@/components/auth/auth-forms";
import { dashboardForRole } from "@/lib/auth/roles";
import { verifySession } from "@/lib/auth/session";

export const metadata = { title: "Đăng ký" };

export default async function RegisterPage() {
  const session = await verifySession();
  if (session.user) redirect(dashboardForRole(session.user.role));
  return <AuthShell eyebrow="BẮT ĐẦU VỚI CVINSIGHT" title="Tạo tài khoản theo đúng vai trò." description="Đăng ký tài khoản Ứng viên hoặc Nhà tuyển dụng. Tài khoản Admin không thể tạo từ giao diện công khai." alternate={{ text: "Đã có tài khoản?", label: "Đăng nhập", href: "/dang-nhap" }}><RegisterForm /></AuthShell>;
}
