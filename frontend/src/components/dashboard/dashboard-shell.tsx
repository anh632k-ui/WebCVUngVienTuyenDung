import Link from "next/link";
import { BrandLink } from "@/components/marketing/site-header";
import type { CurrentUser } from "@/lib/auth/types";
import { getDashboardLinks } from "@/lib/navigation/dashboard-links";
import { DashboardNavigation } from "./dashboard-navigation";
import { LogoutButton } from "./logout-button";

const roleNames = { CANDIDATE: "Ứng viên", HR: "Nhà tuyển dụng", ADMIN: "Quản trị viên" } as const;

export function DashboardShell({ user, children }: { user: CurrentUser; children: React.ReactNode }) {
  return <div className="dashboard-frame">
    <aside className="dashboard-sidebar">
      <BrandLink />
      <div className="dashboard-user"><span>{user.full_name.slice(0, 1).toUpperCase()}</span><div><strong>{user.full_name}</strong><small>{roleNames[user.role]}</small></div></div>
      <DashboardNavigation role={user.role} />
      <LogoutButton />
    </aside>
    <div className="dashboard-main">
      <header className="dashboard-topbar"><div><span>CVInsight Workspace</span><strong>{roleNames[user.role]}</strong></div><Link href="/">Trang công khai</Link></header>
      <main id="noi-dung-chinh" className="dashboard-content">{children}</main>
    </div>
  </div>;
}

export function DashboardWelcome({ user, title, description }: { user: CurrentUser; title: string; description: string }) {
  const links = getDashboardLinks(user.role);
  return <>
    <section className="dashboard-welcome">
      <span className="section-kicker">KHÔNG GIAN LÀM VIỆC</span>
      <h1>{title}</h1>
      <p>Xin chào, <strong>{user.full_name}</strong>. {description}</p>
    </section>
    <section className="dashboard-shortcuts" aria-labelledby="dashboard-actions-heading">
      <h2 id="dashboard-actions-heading">Bắt đầu làm việc</h2>
      <p>Chọn khu vực phù hợp với tài khoản của bạn. Dữ liệu và thao tác thực tế phụ thuộc vào dịch vụ FastAPI.</p>
      <div className="dashboard-shortcut-grid">
        {links.map((item) => <Link key={item.href} className="dashboard-shortcut" href={item.href}>
          <strong>{item.label} <span aria-hidden="true">↗</span></strong>
          <span>{item.description}</span>
        </Link>)}
      </div>
    </section>
  </>;
}
