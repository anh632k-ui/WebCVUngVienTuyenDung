import type { Metadata } from "next";
import Link from "next/link";
import { Icon } from "@/components/marketing/icons";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteHeader } from "@/components/marketing/site-header";

export const metadata: Metadata = {
  title: "Hướng dẫn đọc kết quả phân tích CV và JD",
  description: "Tìm hiểu quy trình trích xuất CV, phân tích mô tả công việc, cách đọc điểm matching và khoảng cách kỹ năng trên CVInsight.",
  alternates: { canonical: "/huong-dan" },
};

const faq = [
  { question: "Điểm phù hợp 86/100 có nghĩa chắc chắn được tuyển?", answer: "Không. Điểm matching chỉ là kết quả tham khảo theo các dữ liệu và trọng số hiện có. Kết quả tuyển dụng luôn cần con người đánh giá dựa trên các yếu tố phù hợp khác." },
  { question: "Tại sao kết quả có thể thay đổi sau khi chỉnh sửa CV hoặc JD?", answer: "Khi dữ liệu đầu vào, tiêu chí hoặc trọng số thay đổi, kết quả cũ có thể không còn đúng. Hệ thống đánh dấu lại kết quả cần tính toán để tránh hiển thị đánh giá đã lỗi thời." },
  { question: "Hệ thống đánh giá những yếu tố nào?", answer: "Ba nhóm chính là kỹ năng, tương đồng ngữ nghĩa giữa CV và JD, và kinh nghiệm có thể định lượng. Mỗi nhóm được tính riêng trước khi tổng hợp theo trọng số của công việc." },
  { question: "HR có xem được mọi CV trong hệ thống không?", answer: "Không. Quyền truy cập được kiểm tra tại backend. HR chỉ được thao tác với CV thuộc phạm vi quyền sở hữu của mình; kết quả self-match riêng tư của ứng viên không được tự động chia sẻ." },
];

export default function GuidePage() {
  return (
    <>
      <SiteHeader />
      <main id="noi-dung-chinh">
        <section className="subpage-hero"><div className="container narrow-container"><span className="section-kicker">HƯỚNG DẪN</span><h1>Hiểu từng bước phân tích. <span>Đọc kết quả đúng cách.</span></h1><p>Hướng dẫn tổng quan về chức năng dành cho ứng viên và HR. Giao diện tài khoản và quy trình thao tác trực tiếp sẽ được bổ sung trong giai đoạn tiếp theo.</p></div></section>
        <section className="section-pad"><div className="container narrow-container article-layout">
          <article className="guide-block"><span className="guide-number">01</span><div><h2>Chuẩn bị CV</h2><p>Sử dụng PDF hoặc DOCX, tối đa 5 MB. Hồ sơ có nội dung văn bản rõ ràng giúp bộ trích xuất đọc thông tin ổn định hơn. Sau khi tải lên, theo dõi trạng thái xử lý và kiểm tra thông tin đã được phân tích.</p></div></article>
          <article className="guide-block"><span className="guide-number">02</span><div><h2>Đối chiếu với JD</h2><p>Mô tả công việc được chuyển thành các tiêu chí kỹ năng và kinh nghiệm. Hệ thống kết hợp tiêu chí này với ngữ nghĩa nội dung CV để tính các điểm thành phần.</p></div></article>
          <article className="guide-block"><span className="guide-number">03</span><div><h2>Đọc kết quả và Skill Gap</h2><p>Điểm tổng phản ánh phép tính theo trọng số, không phải xác suất được tuyển. Hãy xem cả điểm kỹ năng, ngữ nghĩa, kinh nghiệm và các kỹ năng còn thiếu trước khi kết luận.</p></div></article>
        </div></section>
        <section className="faq-section section-pad"><div className="container narrow-container"><div className="section-heading"><span className="section-kicker">CÂU HỎI THƯỜNG GẶP</span><h2>Những điều nên biết <span>trước khi dùng.</span></h2></div><div className="faq-list">{faq.map(item=><details key={item.question}><summary>{item.question}<Icon name="chevron-down" size={19}/></summary><p>{item.answer}</p></details>)}</div><div className="guide-next"><p>Muốn biết hệ thống có những chức năng gì?</p><Link className="inline-link" href="/tinh-nang">Khám phá tính năng <Icon name="arrow-right" size={17}/></Link></div></div></section>
      </main>
      <SiteFooter />
    </>
  );
}
