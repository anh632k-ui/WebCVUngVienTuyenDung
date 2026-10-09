import type { Metadata } from "next";
import Link from "next/link";
import { Icon } from "@/components/marketing/icons";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";

export const metadata: Metadata = {
  title: "Tính năng phân tích CV và đối chiếu JD",
  description: "Khám phá khả năng trích xuất CV, chuẩn hóa kỹ năng, phân tích JD, hybrid matching, Skill Gap và leaderboard theo quyền truy cập của CVInsight.",
  alternates: { canonical: "/tinh-nang" },
};

const modules = [
  { tag: "01 / ỨNG VIÊN", title: "Phân tích CV có kiểm chứng", text: "CV PDF/DOCX được trích xuất văn bản, kỹ năng, kinh nghiệm và học vấn. Người dùng có thể kiểm tra và chỉnh sửa dữ liệu đã nhận diện để cải thiện chất lượng phân tích.", points: ["Hỗ trợ PDF và DOCX tối đa 5 MB", "Chuẩn hóa tên kỹ năng theo taxonomy", "Trạng thái PENDING, PROCESSING, PARSED hoặc FAILED"] },
  { tag: "02 / NHÀ TUYỂN DỤNG", title: "Quản lý và phân tích yêu cầu công việc", text: "HR có thể tạo JD, xác nhận các tiêu chí kỹ năng bắt buộc hoặc tùy chọn, điều chỉnh trọng số và quản lý trạng thái công việc theo quyền sở hữu.", points: ["Kỹ năng mandatory / optional", "Ba trọng số đánh giá có tổng bằng 100%", "Mỗi lần điều chỉnh làm mới kết quả liên quan"] },
  { tag: "03 / KẾT QUẢ", title: "Hybrid Matching và Skill Gap", text: "Điểm tương thích kết hợp kỹ năng, ngữ nghĩa và kinh nghiệm. Các thành phần được trình bày riêng để người dùng có thể hiểu kết quả theo từng tiêu chí.", points: ["Điểm tổng và điểm thành phần 0–100", "Kỹ năng đã đáp ứng và kỹ năng còn thiếu", "Leaderboard giới hạn theo quyền truy cập"] },
];

export default function FeaturesPage() {
  return (
    <>
      <SiteHeader />
      <main id="noi-dung-chinh">
        <section className="subpage-hero"><div className="container narrow-container"><span className="section-kicker">KHÁM PHÁ NỀN TẢNG</span><h1>Những công cụ giúp việc đánh giá <span>rõ ràng hơn.</span></h1><p>CVInsight kết nối dữ liệu hồ sơ, mô tả công việc và kết quả phân tích trong một trải nghiệm dễ theo dõi.</p><Link className="inline-link" href="/huong-dan">Xem hướng dẫn sử dụng <Icon name="arrow-right" size={17} /></Link></div></section>
        <section className="section-pad"><div className="container"><div className="module-list">{modules.map((module) => (
          <article className="module-card" key={module.tag}>
            <div className="module-copy"><span className="section-kicker">{module.tag}</span><h2>{module.title}</h2><p>{module.text}</p></div>
            <ul className="module-points">{module.points.map(point=><li key={point}><span><Icon name="check" size={15} /></span>{point}</li>)}</ul>
          </article>
        ))}</div></div></section>
        <section className="container bottom-note"><Icon name="shield" size={22} /><p>CVInsight là công cụ hỗ trợ phân tích. Điểm matching không đại diện cho cam kết tuyển dụng và không thay thế đánh giá của con người.</p></section>
      </main>
      <SiteFooter />
    </>
  );
}
