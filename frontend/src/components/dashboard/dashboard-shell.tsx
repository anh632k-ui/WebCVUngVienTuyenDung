import Link from "next/link";
import { BrandLink } from "@/components/marketing/site-header";
import { dashboardForRole } from "@/lib/auth/roles";
import type { CurrentUser } from "@/lib/auth/types";
import { LogoutButton } from "./logout-button";

const roleNames = { CANDIDATE: "Ứng viên", HR: "Nhà tuyển dụng", ADMIN: "Quản trị viên" } as const;

export function DashboardShell({ user, children }: { user: CurrentUser; children: React.ReactNode }) {
  return <div className="dashboard-frame"><aside className="dashboard-sidebar"><BrandLink /><div className="dashboard-user"><span>{user.full_name.slice(0, 1).toUpperCase()}</span><div><strong>{user.full_name}</strong><small>{roleNames[user.role]}</small></div></div><nav aria-label="Điều hướng tài khoản"><Link href={dashboardForRole(user.role)}>Tổng quan</Link>{user.role === "CANDIDATE" && <Link href="/cv">Hồ sơ CV</Link>}<Link href="/tai-khoan">Tài khoản</Link><Link href="/doi-mat-khau">Đổi mật khẩu</Link></nav><LogoutButton /></aside><div className="dashboard-main"><header className="dashboard-topbar"><div><span>CVInsight Workspace</span><strong>{roleNames[user.role]}</strong></div><Link href="/">Trang công khai</Link></header><main id="noi-dung-chinh" className="dashboard-content">{children}</main></div></div>;
}

export function DashboardWelcome({ user, title, description, upcoming }: { user: CurrentUser; title: string; description: string; upcoming: string[] }) {
  return <><section className="dashboard-welcome"><span className="section-kicker">KHÔNG GIAN LÀM VIỆC</span><h1>{title}</h1><p>Xin chào, <strong>{user.full_name}</strong>. {description}</p></section><section className="upcoming-panel" aria-labelledby="upcoming-heading"><div><span className="status-pill">F02 · Nền tảng</span><h2 id="upcoming-heading">Các module tiếp theo</h2><p>Những chức năng nghiệp vụ dưới đây chưa được kích hoạt trong F02.</p></div><ul>{upcoming.map((item) => <li key={item}><span>Sắp triển khai</span>{item}</li>)}</ul></section></>;
}
