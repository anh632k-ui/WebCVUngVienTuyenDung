"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { dashboardForRole } from "@/lib/auth/roles";
import type { UserRole } from "@/lib/auth/types";
import { currentDashboardRoute, getDashboardLinks } from "@/lib/navigation/dashboard-links";

export function DashboardNavigation({ role }: { role: UserRole }) {
  const pathname = usePathname();
  const links = [
    { href: dashboardForRole(role), label: "Tổng quan" },
    ...getDashboardLinks(role).map((entry) => ({ href: entry.href, label: entry.label })),
    { href: "/tai-khoan", label: "Tài khoản" },
    { href: "/doi-mat-khau", label: "Đổi mật khẩu" },
  ];

  return <nav className="dashboard-nav" aria-label="Điều hướng tài khoản">
    {links.map((item) => <Link key={item.href} href={item.href}
      aria-current={currentDashboardRoute(pathname, item.href) ? "page" : undefined}>{item.label}</Link>)}
  </nav>;
}
