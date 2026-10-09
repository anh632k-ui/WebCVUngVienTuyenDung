import Link from "next/link";
import { BRAND_NAME } from "@/lib/site-config";
import { Icon } from "./icons";

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
        <Link className="header-action" href="/tinh-nang">
          Khám phá nền tảng <Icon name="arrow-up-right" size={16} />
        </Link>
        <details className="mobile-navigation">
          <summary aria-label="Mở hoặc đóng menu điều hướng">
            <Icon name="menu" size={22} /><span className="sr-only">Menu</span>
          </summary>
          <nav aria-label="Điều hướng trên điện thoại">
            {navigation.map((item) => <Link key={item.href} href={item.href}>{item.label}</Link>)}
            <Link className="mobile-nav-cta" href="/tinh-nang">Khám phá nền tảng</Link>
          </nav>
        </details>
      </div>
    </header>
  );
}
