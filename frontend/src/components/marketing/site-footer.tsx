import Link from "next/link";
import { BrandLink } from "./site-header";

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="container footer-main">
        <div className="footer-brand-column">
          <BrandLink />
          <p>Phân tích hồ sơ và hỗ trợ đánh giá mức độ tương thích công việc bằng AI/NLP.</p>
          <span className="footer-research">Dự án nghiên cứu và phát triển phần mềm.</span>
        </div>
        <nav className="footer-links" aria-label="Thông tin công khai">
          <p className="footer-heading">Khám phá</p>
          <Link href="/tinh-nang">Tính năng</Link>
          <Link href="/huong-dan">Hướng dẫn</Link>
          <Link href="/#quy-trinh">Quy trình phân tích</Link>
          <Link href="/#bao-mat">Quyền riêng tư</Link>
        </nav>
        <div className="footer-note">
          <p className="footer-heading">Lưu ý quan trọng</p>
          <p>Điểm tương thích chỉ mang tính tham khảo, không thay thế đánh giá chuyên môn và không tự động quyết định tuyển dụng.</p>
        </div>
      </div>
      <div className="container footer-bottom">
        <span>© {new Date().getFullYear()} CVInsight. Bản thử nghiệm học thuật.</span>
        <span>Thiết kế cho trải nghiệm rõ ràng và quyền riêng tư.</span>
      </div>
    </footer>
  );
}
