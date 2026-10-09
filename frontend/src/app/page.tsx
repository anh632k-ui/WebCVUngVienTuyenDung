import type { Metadata } from "next";
import Link from "next/link";
import { Icon, type IconName } from "@/components/marketing/icons";
import { ScorePreview } from "@/components/marketing/score-preview";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";

export const metadata: Metadata = {
  alternates: { canonical: "/" },
};

const features: { icon: IconName; title: string; description: string; label: string }[] = [
  { icon: "file-check", title: "Hiểu rõ hồ sơ CV", description: "Trích xuất thông tin từ CV PDF/DOCX và chuẩn hóa dữ liệu kỹ năng, học vấn, kinh nghiệm để dễ kiểm tra.", label: "PHÂN TÍCH CV" },
  { icon: "target", title: "Đối chiếu CV với JD", description: "Kết hợp kỹ năng, ngữ nghĩa và kinh nghiệm để tạo điểm tương thích có thể giải thích.", label: "HYBRID MATCHING" },
  { icon: "chart", title: "Nhìn thấy Skill Gap", description: "Thay vì chỉ xem một con số, bạn có thể nhận diện kỹ năng đã phù hợp và những điểm còn thiếu.", label: "INSIGHT RÕ RÀNG" },
  { icon: "shield", title: "Kiểm soát dữ liệu", description: "Phân quyền Candidate, HR và Admin ngay tại backend; mỗi vai trò chỉ truy cập dữ liệu được phép.", label: "PRIVACY FIRST" },
];

const steps: { step: string; icon: IconName; title: string; text: string }[] = [
  { step: "01", icon: "upload", title: "Cung cấp hồ sơ", text: "Tải CV PDF hoặc DOCX và theo dõi trạng thái xử lý. Bạn có thể kiểm tra thông tin đã được trích xuất." },
  { step: "02", icon: "workflow", title: "Đối chiếu yêu cầu", text: "Chọn JD phù hợp. Hệ thống phân tích kỹ năng, ngữ nghĩa và kinh nghiệm theo cùng bộ quy tắc." },
  { step: "03", icon: "chart", title: "Đọc kết quả", text: "Xem điểm từng thành phần, các kỹ năng phù hợp và Skill Gap để tham khảo khi cải thiện CV." },
];

export default function HomePage() {
  return (
    <>
      <SiteHeader />
      <main id="noi-dung-chinh">
        <section className="hero section-shell" aria-labelledby="hero-title">
          <div className="hero-atmosphere" aria-hidden="true" />
          <div className="container hero-grid">
            <div className="hero-copy">
              <div className="eyebrow-pill"><span className="eyebrow-spark"><Icon name="sparkles" size={13} /></span> AI + NLP CHO HỒ SƠ NGHỀ NGHIỆP <span className="eyebrow-line" /></div>
              <h1 id="hero-title">Hiểu năng lực. <span>Khám phá mức độ phù hợp.</span></h1>
              <p className="hero-description">Phân tích CV, đối chiếu mô tả công việc và nhận diện khoảng cách kỹ năng trên một nền tảng trực quan — hỗ trợ ứng viên và nhà tuyển dụng đưa ra đánh giá có cơ sở hơn.</p>
              <div className="hero-actions">
                <Link className="button button-primary" href="/tinh-nang">Khám phá tính năng <Icon name="arrow-up-right" size={18} /></Link>
                <Link className="button button-quiet" href="/huong-dan">Cách nền tảng hoạt động <Icon name="arrow-right" size={18} /></Link>
              </div>
              <div className="hero-assurances">
                <span><Icon name="check" size={15} /> Hỗ trợ CV tiếng Việt và tiếng Anh</span>
                <span><Icon name="check" size={15} /> Không tự quyết định tuyển dụng</span>
              </div>
            </div>
            <ScorePreview />
          </div>
          <div className="container hero-bottom">
            <div className="hero-bottom-line" />
            <span>MỘT QUY TRÌNH RÕ RÀNG, TỪ DỮ LIỆU ĐẾN INSIGHT</span>
            <Icon name="chevron-down" size={17} />
          </div>
        </section>

        <section className="features-section section-pad" id="tinh-nang" aria-labelledby="features-heading">
          <div className="container">
            <div className="section-heading split-heading">
              <div><span className="section-kicker">GIẢI PHÁP</span><h2 id="features-heading">Mọi thông tin cần thiết,<br /><span>trong một luồng phân tích.</span></h2></div>
              <p>Tập trung vào thông tin có thể hành động, tránh biến phân tích CV thành những bảng số liệu khó hiểu.</p>
            </div>
            <div className="feature-grid">
              {features.map((feature, index) => (
                <article className={"feature-card feature-card-" + index} key={feature.title}>
                  <div className="feature-card-icon"><Icon name={feature.icon} size={23} /></div>
                  <span className="feature-label">{feature.label}</span>
                  <h3>{feature.title}</h3>
                  <p>{feature.description}</p>
                  <div className="feature-card-bottom"><span>Khả năng của nền tảng</span><Icon name="arrow-up-right" size={17} /></div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="process-section section-pad" id="quy-trinh" aria-labelledby="process-heading">
          <div className="container">
            <div className="section-heading center-heading"><span className="section-kicker">QUY TRÌNH</span><h2 id="process-heading">Ba bước để nhìn rõ hơn<br /><span>mức độ tương thích.</span></h2><p>Giao diện được thiết kế để người dùng hiểu điều gì đang diễn ra ở mỗi giai đoạn.</p></div>
            <div className="process-grid">
              {steps.map((step) => (
                <article className="process-card" key={step.step}>
                  <div className="process-top"><span>{step.step}</span><Icon name={step.icon} size={24} /></div>
                  <h3>{step.title}</h3><p>{step.text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="privacy-section section-pad" id="bao-mat" aria-labelledby="privacy-heading">
          <div className="container privacy-grid">
            <div className="privacy-visual" aria-hidden="true">
              <div className="privacy-halo"><div className="privacy-inner"><Icon name="lock" size={48} /></div></div>
              <div className="privacy-floating privacy-one"><Icon name="file-check" size={18} /> Hồ sơ</div>
              <div className="privacy-floating privacy-two"><Icon name="shield" size={18} /> Quyền truy cập</div>
            </div>
            <div className="privacy-copy"><span className="section-kicker">MINH BẠCH & RIÊNG TƯ</span><h2 id="privacy-heading">AI hỗ trợ phân tích.<br /><span>Con người giữ quyền quyết định.</span></h2><p>CV và kết quả phân tích là dữ liệu nhạy cảm. Hệ thống sử dụng xác thực và kiểm tra quyền truy cập theo vai trò; kết quả matching chỉ dành cho những tài khoản được phép.</p><div className="privacy-points"><span><Icon name="check" size={16} /> Phân quyền kiểm soát tại backend</span><span><Icon name="check" size={16} /> Kết quả không phải quyết định tuyển dụng tự động</span><span><Icon name="check" size={16} /> Có thể kiểm tra và điều chỉnh dữ liệu trích xuất</span></div><Link className="inline-link" href="/huong-dan">Tìm hiểu quy trình <Icon name="arrow-right" size={17} /></Link></div>
          </div>
        </section>

        <section className="final-cta section-pad" aria-labelledby="final-heading">
          <div className="container"><div className="cta-panel"><div className="cta-glow" aria-hidden="true" /><div><span className="cta-kicker">CVINSIGHT</span><h2 id="final-heading">Một góc nhìn rõ ràng hơn<br />về hồ sơ và công việc.</h2><p>Tìm hiểu các chức năng đã được thiết kế cho ứng viên và nhà tuyển dụng.</p></div><Link className="button button-light" href="/tinh-nang">Xem toàn bộ tính năng <Icon name="arrow-up-right" size={18} /></Link></div></div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
