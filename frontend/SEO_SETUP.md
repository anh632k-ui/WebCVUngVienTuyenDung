# Thiết lập SEO cho CVInsight

## Phần đã được tự động hóa

Frontend đã có metadata riêng cho `/`, `/tinh-nang` và `/huong-dan`; thẻ Open Graph/Twitter; ảnh Open Graph 1200×630; `lang="vi"`; JSON-LD `WebSite`; `robots.txt`; `sitemap.xml`; canonical URL; và cơ chế `noindex` an toàn theo cấu hình.

Mặc định, khi chưa có domain HTTPS hợp lệ hoặc chưa bật cờ SEO, các trang phát `noindex, follow`, không có canonical, absolute social URL, JSON-LD dựa trên production origin và sitemap rỗng. Ngay cả khi `SITE_URL` hợp lệ, origin này cũng không được công khai nếu `SEO_INDEXING_ENABLED` chưa bằng `true`. `robots.txt` vẫn cho phép crawler đọc trang để nhận thẻ `noindex`. Đây không phải cơ chế bảo mật cho route riêng tư.

## Cấu hình production

Tạo biến môi trường phía server/deployment dựa trên `.env.example`:

```dotenv
SITE_URL=https://ten-mien-chinh-thuc.vn
SEO_INDEXING_ENABLED=true
```

`SITE_URL` phải là HTTPS origin thật, không có path, query, hash hay thông tin đăng nhập. Không dùng `localhost`, staging URL hoặc domain ví dụ. Chỉ bật `SEO_INDEXING_ENABLED=true` sau khi production đã sẵn sàng được lập chỉ mục. Thay đổi biến môi trường cần build/deploy lại ứng dụng.

## Kiểm tra sau deploy

1. Mở source HTML của ba trang và kiểm tra title, description, Open Graph, Twitter card, canonical và robots meta.
2. Mở `/robots.txt`; khi SEO bật, tệp phải chứa URL sitemap thuộc đúng production origin.
3. Mở `/sitemap.xml`; chỉ có `/`, `/tinh-nang`, `/huong-dan`.
4. Mở `/og` hoặc URL ảnh được ghi trong `og:image`; ảnh phải trả `image/png` và render ở kích thước 1200×630.
5. Chạy Rich Results Test hoặc Schema Markup Validator để kiểm tra JSON-LD `WebSite`.
6. Chạy Lighthouse ở mobile và desktop để theo dõi SEO, accessibility và Core Web Vitals.

Trước khi bật SEO, kiểm tra lại với `SEO_INDEXING_ENABLED=false`: không có canonical production, trang có `noindex` và sitemap không chứa URL.

Các kiểm tra tự động có thể chạy bằng `npm test`. Lệnh này build và khởi động production server ở cả hai chế độ SEO OFF/ON, kiểm tra metadata HTML, robots, sitemap, 404 và ảnh OG. Domain `.test` trong test chỉ là fixture dành riêng cho kiểm thử, không được dùng làm cấu hình deployment.

## Google Search Console

Chủ dự án cần tự thực hiện các bước sau bằng tài khoản Google của mình; không chia sẻ mật khẩu hoặc token:

1. Mở Google Search Console và thêm Domain property (khuyến nghị) hoặc URL-prefix property cho production origin.
2. Xác minh quyền sở hữu. Với Domain property, thêm TXT record do Google cung cấp vào DNS. Với URL-prefix, có thể dùng DNS hoặc file/thẻ xác minh theo hướng dẫn của Google.
3. Sau khi xác minh và đã bật indexing, vào **Sitemaps**, nhập `sitemap.xml` rồi gửi.
4. Dùng **URL Inspection** để kiểm tra ba URL public và yêu cầu lập chỉ mục khi cần.
5. Theo dõi **Page indexing** và **Core Web Vitals**; xử lý URL lỗi, canonical sai hoặc trải nghiệm trang kém trước khi gửi lại.

## Chưa thể hoàn tất khi chưa có domain

- Không thể tạo canonical URL và sitemap production có ý nghĩa.
- Không thể bật index an toàn hoặc xác nhận trạng thái index thật.
- Không thể xác minh Search Console, gửi sitemap hay xem báo cáo Core Web Vitals thực tế.
- Không thể kiểm tra card chia sẻ mạng xã hội từ một URL production công khai.

Không thêm route Auth, Dashboard, CV, Matching, Admin hoặc API riêng tư vào sitemap. Khi các route đó xuất hiện, chúng phải được bảo vệ bằng authentication/authorization thực sự, không chỉ bằng robots hoặc `noindex`.
