import Link from "next/link";
import type { Metadata } from "next";
import type { SessionFailure } from "@/lib/auth/session-errors";

export const metadata: Metadata = { title: "Trạng thái phiên đăng nhập" };

const messages: Record<Exclude<SessionFailure, "unauthenticated">, { title: string; description: string }> = {
  inactive: {
    title: "Tài khoản không được phép sử dụng",
    description: "Tài khoản của bạn hiện không hoạt động. Vui lòng liên hệ quản trị viên nếu bạn cho rằng đây là nhầm lẫn.",
  },
  unavailable: {
    title: "Dịch vụ xác thực tạm thời gián đoạn",
    description: "CVInsight chưa thể xác minh phiên của bạn lúc này. Cookie đăng nhập vẫn được giữ nguyên; vui lòng thử lại sau ít phút.",
  },
  upstream: {
    title: "Không thể xác minh phiên đăng nhập",
    description: "Dịch vụ xác thực trả về phản hồi không hợp lệ. CVInsight chưa hiển thị dữ liệu tài khoản và không coi phiên của bạn là đã hết hạn.",
  },
};

export default async function SessionStatusPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const reason = typeof query.reason === "string" && query.reason in messages ? query.reason as keyof typeof messages : "upstream";
  const message = messages[reason];
  return (
    <main id="noi-dung-chinh" className="status-page">
      <div className="status-card session-status-card">
        <span className="section-kicker">TRẠNG THÁI PHIÊN</span>
        <h1>{message.title}</h1>
        <p>{message.description}</p>
        <div className="session-status-actions">
          {reason !== "inactive" && <Link className="button button-primary" href="/dashboard">Thử lại</Link>}
          <Link className="button button-secondary" href="/dang-nhap">Về trang đăng nhập</Link>
        </div>
      </div>
    </main>
  );
}
