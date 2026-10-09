# F06 – UI Polish & Accessibility (source + CI)

- Dashboard cập nhật shortcut theo role dựa trên các route frontend thực tế (F03–F05), không còn hiển thị thông tin sai rằng tất cả module còn đang chờ F02.
- Sidebar có aria-current=page, trạng thái active được tạo từ path client không phụ thuộc JWT; phân quyền SSR vẫn giữ ở backend/Next.js session.
- Menu dashboard mobile được bố trí hàng ngang có thể cuộn, tránh mỗi liên kết chiếm một hàng dài ở màn 320px.
- Các lối tắt rõ chức năng đã có giao diện, không hứa backend/worker/AI chạy thật khi thiếu dịch vụ.
- Unit tests kiểm tra phân vai shortcut và nested active-link; frontend CI chạy toàn bộ regression như cũ.

Cần đánh giá thủ công sau: hình ảnh/tràn layout 320/375/768/1024/1440px, keyboard/focus và VoiceOver/NVDA nếu có; browser E2E kết nối FastAPI/PostgreSQL/worker thật. CI source/HTTP mock không thay thế browser visual.
