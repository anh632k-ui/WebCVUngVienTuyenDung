import Link from "next/link";
import { Icon } from "@/components/marketing/icons";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";
import { createPageMetadata } from "@/lib/metadata";

export const metadata = createPageMetadata({
  title: "Hướng dẫn chuẩn bị CV và đọc kết quả matching",
  description: "Cách chuẩn bị CV PDF/DOCX, hiểu quy trình trích xuất, ba nhóm điểm, Skill Gap và nguyên tắc quyền riêng tư trên CVInsight.",
  path: "/huong-dan",
});

const faq = [
  { question: "Điểm phù hợp cao có nghĩa chắc chắn được tuyển?", answer: "Không. Điểm matching là kết quả tham khảo theo dữ liệu và trọng số hiện có, không phải xác suất trúng tuyển. Quyết định tuyển dụng luôn cần con người đánh giá thêm." },
  { question: "Tại sao kết quả thay đổi sau khi sửa CV hoặc JD?", answer: "Khi dữ liệu đầu vào, tiêu chí hoặc trọng số thay đổi, kết quả cũ trở nên lỗi thời. Hệ thống đánh dấu để tính lại trên phiên bản dữ liệu mới." },
  { question: "Hệ thống đánh giá những yếu tố nào?", answer: "Ba nhóm chính là kỹ năng, tương đồng ngữ nghĩa giữa CV và JD, và kinh nghiệm có thể định lượng. Mỗi nhóm được tính riêng rồi tổng hợp theo trọng số của JD." },
  { question: "HR có xem được mọi CV trong hệ thống không?", answer: "Không. HR chỉ thao tác với CV trong talent pool thuộc sở hữu của mình. Candidate self-match với JD của HR không cấp quyền cho HR xem CV hoặc kết quả đó." },
];

export default function GuidePage() {
  return (
    <>
      <SiteHeader />
      <main id="noi-dung-chinh">
        <section className="subpage-hero"><div className="container narrow-container"><span className="section-kicker">HƯỚNG DẪN</span><h1>Chuẩn bị đúng dữ liệu. <span>Đọc kết quả đúng cách.</span></h1><p>Đây là hướng dẫn tổng quan trước khi dashboard được triển khai. Không có thao tác tải CV hoặc kết nối API nghiệp vụ trong F01.</p></div></section>
        <section className="section-pad" aria-label="Quy trình sử dụng"><div className="container narrow-container article-layout">
          <article className="guide-block"><span className="guide-number">01</span><div><h2>Chuẩn bị CV PDF/DOCX</h2><p>Dùng tệp PDF hoặc DOCX tối đa 5 MB. Ưu tiên văn bản rõ ràng, tiêu đề mục nhất quán và mốc thời gian đầy đủ; ảnh scan cần OCR thuộc phạm vi nâng cao và chưa phải cam kết của MVP.</p></div></article>
          <article className="guide-block"><span className="guide-number">02</span><div><h2>Kiểm tra dữ liệu trích xuất</h2><p>CV đi qua các trạng thái chờ, xử lý, hoàn tất hoặc thất bại. Khi hoàn tất, người dùng cần kiểm tra kỹ năng, kinh nghiệm và học vấn được nhận diện trước khi matching.</p></div></article>
          <article className="guide-block"><span className="guide-number">03</span><div><h2>Hiểu ba nhóm điểm</h2><p>Skill Score đo mức bao phủ kỹ năng; Semantic Score đo tương đồng nội dung; Experience Score dùng các khoảng thời gian có thể định lượng. Overall Score là tổng có trọng số của ba nhóm, không phải xác suất tuyển dụng.</p></div></article>
          <article className="guide-block"><span className="guide-number">04</span><div><h2>Đọc Skill Gap</h2><p>Skill Gap tách kỹ năng đã đáp ứng và còn thiếu so với tiêu chí JD. Hãy xem đây là gợi ý để kiểm tra hoặc cải thiện hồ sơ, không phải kết luận tuyệt đối về năng lực.</p></div></article>
          <article className="guide-block" id="quyen-rieng-tu"><span className="guide-number">05</span><div><h2>Giữ quyền riêng tư</h2><p>Candidate và HR chỉ truy cập tài nguyên trong phạm vi sở hữu. Kết quả self-match của Candidate là riêng tư; việc chọn JD của HR không đồng nghĩa chia sẻ CV cho HR. Các route riêng tư về sau phải được bảo vệ bằng xác thực và phân quyền thật.</p></div></article>
        </div></section>
        <section className="faq-section section-pad" aria-labelledby="faq-heading"><div className="container narrow-container"><div className="section-heading"><span className="section-kicker">CÂU HỎI THƯỜNG GẶP</span><h2 id="faq-heading">Điều nên biết <span>trước khi sử dụng.</span></h2></div><div className="faq-list">{faq.map((item) => <details key={item.question}><summary>{item.question}<Icon name="chevron-down" size={19} /></summary><p>{item.answer}</p></details>)}</div><div className="guide-next"><p>Muốn hiểu rõ phạm vi của từng vai trò?</p><Link className="inline-link" href="/tinh-nang">Khám phá tính năng <Icon name="arrow-right" size={17} /></Link></div></div></section>
      </main>
      <SiteFooter />
    </>
  );
}
