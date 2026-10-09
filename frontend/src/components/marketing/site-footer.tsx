import Link from "next/link";
import { Icon } from "./icons";

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="container footer-main">
        <div className="footer-brand-column">
          <Link className="brand" href="/" aria-label="CVInsight - Về trang chủ">
            <span className="brand-mark"><Icon name="sparkles" size={20} /></span>
            <span>CV<span className="brand-accent">Insight</span></span>
          </Link>
          <p>Phân tích hồ sơ và hỗ trợ đánh giá mức độ tương thích công việc bằng AI/NLP.</p>
          <span className="footer-research">Dự án nghiên cứu và phát triển phần mềm.</span>
        </div>
        <nav className="footer-links" aria-label="Thông tin công khai">
          <p className="footer-heading">Khám phá</p>
          <Link href="/tinh-nang">Tính năng</Link>
          <Link href="/huong-dan">Hướng dẫn</Link>
          <Link href="/#quy-trinh">Quy trình phân tích</Link>
        </nav>
        <div className="footer-note">
          <p className="footer-heading">Lưu ý quan trọng</p>
          <p>Điểm tương thích chỉ mang tính tham khảo, không thay thế đánh giá chuyên môn và không tự động quyết định tuyển dụng.</p>
        </div>
      </div>
      <div className="container footer-bottom">
        <span>© {new Date().getFullYear()} CVInsight. Bản thử nghiệm học thuật.</span>
        <span>Thiết kế cho trải nghiệm đọc rõ ràng và quyền riêng tư.</span>
      </div>
    </footer>
  );
}
