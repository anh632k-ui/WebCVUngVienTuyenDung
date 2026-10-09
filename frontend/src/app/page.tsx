import Link from "next/link";
import { Icon, type IconName } from "@/components/marketing/icons";
import { ScorePreview } from "@/components/marketing/score-preview";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";
import { createPageMetadata, createWebsiteJsonLd } from "@/lib/metadata";
import { PUBLIC_PAGES } from "@/lib/site-config";

export const metadata = createPageMetadata(PUBLIC_PAGES.home);

const features: { icon: IconName; title: string; description: string; label: string }[] = [
  { icon: "file-check", title: "Hiểu rõ hồ sơ CV", description: "Trích xuất thông tin từ CV PDF/DOCX và chuẩn hóa dữ liệu kỹ năng, học vấn, kinh nghiệm để người dùng kiểm tra.", label: "PHÂN TÍCH CV" },
  { icon: "target", title: "Đối chiếu CV với JD", description: "Kết hợp kỹ năng, ngữ nghĩa và kinh nghiệm để tạo điểm tương thích có các thành phần rõ ràng.", label: "HYBRID MATCHING" },
  { icon: "chart", title: "Nhìn thấy Skill Gap", description: "Nhận diện kỹ năng đã phù hợp và những tiêu chí còn thiếu thay vì chỉ xem một con số tổng.", label: "INSIGHT RÕ RÀNG" },
  { icon: "shield", title: "Kiểm soát dữ liệu", description: "Candidate, HR và Admin có phạm vi truy cập riêng; quyền sở hữu được kiểm tra tại backend.", label: "PRIVACY FIRST" },
];

const steps: { step: string; icon: IconName; title: string; text: string }[] = [
  { step: "01", icon: "upload", title: "Chuẩn bị CV", text: "Cung cấp CV PDF hoặc DOCX, theo dõi trạng thái xử lý và kiểm tra thông tin được trích xuất." },
  { step: "02", icon: "workflow", title: "Đối chiếu JD", text: "Chọn mô tả công việc phù hợp để hệ thống phân tích kỹ năng, ngữ nghĩa và kinh nghiệm." },
  { step: "03", icon: "chart", title: "Đọc kết quả", text: "Xem từng nhóm điểm, kỹ năng phù hợp và Skill Gap như nguồn tham khảo cho quyết định của con người." },
];

export default function HomePage() {
  const websiteJsonLd = createWebsiteJsonLd();

  return (
    <>
      {websiteJsonLd && <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(websiteJsonLd).replace(/</g, "\\u003c") }} />}
      <SiteHeader />
      <main id="noi-dung-chinh">
        <section className="hero section-shell" aria-labelledby="hero-title">
          <div className="hero-atmosphere" aria-hidden="true" />
          <div className="container hero-grid">
            <div className="hero-copy">
              <div className="eyebrow-pill"><span className="eyebrow-spark"><Icon name="sparkles" size={13} /></span> AI + NLP CHO HỒ SƠ NGHỀ NGHIỆP</div>
              <h1 id="hero-title">Hiểu năng lực. <span>Khám phá mức độ phù hợp.</span></h1>
              <p className="hero-description">Phân tích CV, đối chiếu mô tả công việc và nhận diện khoảng cách kỹ năng trên một nền tảng trực quan — hỗ trợ ứng viên và nhà tuyển dụng đánh giá có cơ sở hơn.</p>
              <div className="hero-actions">
                <Link className="button button-primary" href="/tinh-nang">Khám phá tính năng <Icon name="arrow-up-right" size={18} /></Link>
                <Link className="button button-secondary" href="/huong-dan">Xem hướng dẫn <Icon name="arrow-right" size={18} /></Link>
              </div>
              <div className="hero-assurances"><span><Icon name="check" size={15} /> CV tiếng Việt và tiếng Anh</span><span><Icon name="check" size={15} /> Con người giữ quyền quyết định</span></div>
            </div>
            <ScorePreview />
          </div>
        </section>

        <section className="features-section section-pad" id="tinh-nang" aria-labelledby="features-heading">
          <div className="container">
            <div className="section-heading split-heading"><div><span className="section-kicker">GIẢI PHÁP</span><h2 id="features-heading">Thông tin cần thiết, <span>trong một luồng phân tích.</span></h2></div><p>Tập trung vào dữ liệu có thể kiểm tra, tránh biến phân tích CV thành những bảng số liệu khó hiểu.</p></div>
            <div className="feature-grid">{features.map((feature) => <article className="feature-card" key={feature.title}><div className="feature-card-icon"><Icon name={feature.icon} size={23} /></div><span className="feature-label">{feature.label}</span><h3>{feature.title}</h3><p>{feature.description}</p></article>)}</div>
          </div>
        </section>

        <section className="process-section section-pad" id="quy-trinh" aria-labelledby="process-heading">
          <div className="container"><div className="section-heading center-heading"><span className="section-kicker">QUY TRÌNH</span><h2 id="process-heading">Ba bước để nhìn rõ hơn <span>mức độ tương thích.</span></h2><p>Mỗi giai đoạn đều cho người dùng biết dữ liệu đang được xử lý như thế nào.</p></div><div className="process-grid">{steps.map((step) => <article className="process-card" key={step.step}><div className="process-top"><span>{step.step}</span><Icon name={step.icon} size={24} /></div><h3>{step.title}</h3><p>{step.text}</p></article>)}</div></div>
        </section>

        <section className="privacy-section section-pad" id="bao-mat" aria-labelledby="privacy-heading">
          <div className="container privacy-grid">
            <div className="privacy-visual" aria-hidden="true"><div className="privacy-halo"><div className="privacy-inner"><Icon name="lock" size={46} /></div></div><div className="privacy-floating privacy-one"><Icon name="file-check" size={18} /> Hồ sơ</div><div className="privacy-floating privacy-two"><Icon name="shield" size={18} /> Quyền truy cập</div></div>
            <div className="privacy-copy"><span className="section-kicker">MINH BẠCH & RIÊNG TƯ</span><h2 id="privacy-heading">AI hỗ trợ phân tích. <span>Con người giữ quyền quyết định.</span></h2><p>CV và kết quả phân tích là dữ liệu nhạy cảm. Candidate self-match không tự động chia sẻ CV hoặc kết quả riêng tư cho HR.</p><div className="privacy-points"><span><Icon name="check" size={16} /> Quyền sở hữu được kiểm tra tại backend</span><span><Icon name="check" size={16} /> Kết quả không tự động quyết định tuyển dụng</span><span><Icon name="check" size={16} /> Người dùng có thể kiểm tra dữ liệu trích xuất</span></div><Link className="inline-link" href="/huong-dan#quyen-rieng-tu">Tìm hiểu quyền riêng tư <Icon name="arrow-right" size={17} /></Link></div>
          </div>
        </section>

        <section className="final-cta section-pad" aria-labelledby="final-heading"><div className="container"><div className="cta-panel"><div><span className="cta-kicker">CVINSIGHT</span><h2 id="final-heading">Một góc nhìn rõ ràng hơn về hồ sơ và công việc.</h2><p>Tìm hiểu phạm vi MVP dành cho ứng viên và nhà tuyển dụng.</p></div><Link className="button button-light" href="/tinh-nang">Xem toàn bộ tính năng <Icon name="arrow-up-right" size={18} /></Link></div></div></section>
      </main>
      <SiteFooter />
    </>
  );
}
