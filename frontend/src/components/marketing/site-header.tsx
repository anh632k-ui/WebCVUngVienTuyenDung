import Link from "next/link";
import { BRAND_NAME } from "@/lib/site-config";
import { Icon } from "./icons";
import { MobileNavigation } from "./mobile-navigation";

const navigation = [
  { href: "/tinh-nang", label: "Tính năng" },
  { href: "/huong-dan", label: "Hướng dẫn" },
  { href: "/#quy-trinh", label: "Quy trình" },
  { href: "/#bao-mat", label: "Quyền riêng tư" },
];

export function BrandLink() {
  return (
    <Link className="brand" href="/" aria-label={`${BRAND_NAME} — Về trang chủ`}>
      <span className="brand-mark"><Icon name="sparkles" size={19} /></span>
      <span>CV<span className="brand-accent">Insight</span></span>
    </Link>
  );
}

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="container header-inner">
        <BrandLink />
        <nav className="desktop-navigation" aria-label="Điều hướng chính">
          {navigation.map((item) => <Link key={item.href} href={item.href}>{item.label}</Link>)}
        </nav>
        <div className="header-auth-actions">
          <Link className="header-login" href="/dang-nhap">Đăng nhập</Link>
          <Link className="header-action" href="/dang-ky">Đăng ký <Icon name="arrow-up-right" size={16} /></Link>
        </div>
        <MobileNavigation items={navigation} />
      </div>
    </header>
  );
}
