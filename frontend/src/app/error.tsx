"use client";

import Link from "next/link";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="noi-dung-chinh" className="status-page">
      <div className="status-card">
        <span className="status-code">Lỗi</span>
        <h1>Đã có sự cố xảy ra</h1>
        <p>Bạn có thể thử tải lại nội dung hoặc quay về trang chủ.</p>
        <div className="status-actions">
          <button className="button button-primary" type="button" onClick={() => reset()}>Thử lại</button>
          <Link className="button button-secondary" href="/">Về trang chủ</Link>
        </div>
      </div>
    </main>
  );
}
