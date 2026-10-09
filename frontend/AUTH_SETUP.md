# Thiết lập Auth F02

Frontend dùng mô hình BFF: trình duyệt chỉ gọi các route `/api/session/*` của Next.js. Next.js gọi FastAPI và lưu access token trong cookie host-only, `HttpOnly`, `SameSite=Lax`, `Path=/`; production bật `Secure`. Thời gian sống cookie lấy từ `expires_in` của backend và không được tự gia hạn.

## Cấu hình local

Sao chép `.env.example` thành `.env.local` và đặt:

```dotenv
BACKEND_API_URL=http://127.0.0.1:8000
SITE_URL=https://your-production-domain.example
SEO_INDEXING_ENABLED=false
```

`BACKEND_API_URL` chỉ được đọc ở server, không dùng tiền tố `NEXT_PUBLIC_`. Giá trị phải là HTTP/HTTPS origin thuần, không kèm `/api/v1`, pathname, thông tin đăng nhập, query hoặc fragment. Chạy FastAPI tại cổng 8000 theo hướng dẫn của backend, sau đó:

```powershell
cd frontend
npm ci
npm run dev
```

Mở `http://localhost:3000`. Các trang tài khoản nằm ở `/dang-nhap`, `/dang-ky`, `/dashboard`, `/tai-khoan` và `/doi-mat-khau`.

## Giới hạn phiên MVP

Backend hiện chỉ phát access token, không có refresh token hay endpoint revoke/logout. Đăng xuất và đổi mật khẩu thành công sẽ xóa cookie ở trình duyệt; JWT đã phát không bị backend thu hồi trước khi hết hạn. Protected pages luôn xác minh phiên qua FastAPI `/users/me` và không dựa riêng vào sự tồn tại của cookie.
