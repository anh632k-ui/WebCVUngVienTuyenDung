import Link from "next/link";
import { BrandLink } from "@/components/marketing/site-header";

export function AuthShell({ eyebrow, title, description, children, alternate }: { eyebrow: string; title: string; description: string; children: React.ReactNode; alternate: { text: string; label: string; href: string } }) {
  return (
    <main id="noi-dung-chinh" className="auth-page">
      <section className="auth-aside" aria-label="Giới thiệu CVInsight">
        <BrandLink />
        <div><span className="section-kicker">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
        <p className="auth-security-note">Phiên đăng nhập được lưu bằng cookie HttpOnly. CVInsight không lưu mật khẩu trên trình duyệt.</p>
      </section>
      <section className="auth-form-panel" aria-label={title}>
        <div className="auth-mobile-brand"><BrandLink /></div>
        {children}
        <p className="auth-alternate">{alternate.text} <Link href={alternate.href}>{alternate.label}</Link></p>
        <Link className="auth-back-link" href="/">← Về trang chủ</Link>
      </section>
    </main>
  );
}
