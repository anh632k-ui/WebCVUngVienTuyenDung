import Link from "next/link";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main id="noi-dung-chinh" className="status-page">
        <div className="status-card">
          <span className="status-code">404</span>
          <h1>Không tìm thấy trang</h1>
          <p>Địa chỉ này không tồn tại hoặc đã được thay đổi.</p>
          <Link className="button button-primary" href="/">Quay về trang chủ</Link>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
