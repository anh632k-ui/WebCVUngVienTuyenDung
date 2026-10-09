import Link from "next/link";
import { Icon, type IconName } from "@/components/marketing/icons";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";
import { createPageMetadata } from "@/lib/metadata";

export const metadata = createPageMetadata({
  title: "Tính năng phân tích CV và đối chiếu JD",
  description: "Khám phá phạm vi MVP: phân tích CV, tiêu chí JD, hybrid matching, Skill Gap, leaderboard và phân quyền Candidate, HR, Admin.",
  path: "/tinh-nang",
});

const modules: { tag: string; icon: IconName; title: string; text: string; points: string[] }[] = [
  { tag: "01 / ỨNG VIÊN", icon: "file-check", title: "CV và self-matching riêng tư", text: "Candidate quản lý CV của mình, kiểm tra và chỉnh sửa dữ liệu trích xuất, sau đó tự đối chiếu với JD đang hoạt động. Các thao tác này thuộc dashboard ở giai đoạn tiếp theo.", points: ["PDF/DOCX tối đa 5 MB", "Human-in-the-loop cho dữ liệu trích xuất", "Kết quả self-match không tự động chia sẻ cho HR"] },
  { tag: "02 / NHÀ TUYỂN DỤNG", icon: "workflow", title: "JD, tiêu chí và talent pool", text: "HR quản lý JD và kho CV thuộc quyền sở hữu của mình, xác nhận tiêu chí, điều chỉnh ba trọng số rồi thực hiện batch matching khi dữ liệu sẵn sàng.", points: ["Kỹ năng bắt buộc hoặc tùy chọn", "Trọng số kỹ năng, ngữ nghĩa, kinh nghiệm", "Leaderboard chỉ trong talent pool của HR"] },
  { tag: "03 / HYBRID MATCHING", icon: "target", title: "Ba góc nhìn, một điểm tổng", text: "Hybrid-v1 kết hợp Skill Score, Semantic Score và Experience Score. Overall Score là tổng có trọng số; Skill Gap cho biết kỹ năng đã đáp ứng và còn thiếu.", points: ["Điểm thành phần và điểm tổng từ 0–100", "Ngữ nghĩa chỉ so sánh khi model tương thích", "Điểm số là tham khảo, không phải xác suất trúng tuyển"] },
  { tag: "04 / PHÂN QUYỀN", icon: "shield", title: "Ranh giới dữ liệu rõ ràng", text: "Candidate, HR và Admin có trách nhiệm khác nhau. Backend kiểm tra vai trò, ownership và trạng thái tài nguyên; robots/noindex không được xem là biện pháp bảo mật.", points: ["Candidate chỉ dùng CV của mình", "HR chỉ dùng JD và CV trong kho của mình", "Admin giám sát theo quyền quản trị"] },
];

export default function FeaturesPage() {
  return (
    <>
      <SiteHeader />
      <main id="noi-dung-chinh">
        <section className="subpage-hero"><div className="container narrow-container"><span className="section-kicker">KHÁM PHÁ NỀN TẢNG</span><h1>Công cụ hỗ trợ đánh giá <span>rõ ràng và có kiểm soát.</span></h1><p>Trang này mô tả phạm vi MVP đã thiết kế. Giao diện thao tác tài khoản, CV, JD và matching sẽ được triển khai ở các giai đoạn tiếp theo.</p><Link className="inline-link" href="/huong-dan">Xem hướng dẫn đọc kết quả <Icon name="arrow-right" size={17} /></Link></div></section>
        <section className="section-pad" aria-label="Nhóm tính năng"><div className="container"><div className="module-list">{modules.map((module) => <article className="module-card" key={module.tag}><div className="module-icon"><Icon name={module.icon} size={23} /></div><div className="module-copy"><span className="section-kicker">{module.tag}</span><h2>{module.title}</h2><p>{module.text}</p></div><ul className="module-points">{module.points.map((point) => <li key={point}><span><Icon name="check" size={15} /></span>{point}</li>)}</ul></article>)}</div></div></section>
        <section className="container bottom-note" aria-label="Lưu ý"><Icon name="shield" size={22} /><p>CVInsight là công cụ hỗ trợ phân tích. Điểm matching không đại diện cho cam kết tuyển dụng và không thay thế đánh giá của con người.</p></section>
      </main>
      <SiteFooter />
    </>
  );
}
