# F05 – Giao diện Matching Candidate/HR

BFF /api/workspace/matching/* nhận JSON từ browser, đọc JWT cookie HttpOnly F02 trên Next.js server, xác minh Candidate/HR qua /users/me, rồi gọi FastAPI bằng Bearer token chỉ ở server. Backend kiểm owner, current generation/snapshot và visibility cuối cùng.

- Candidate: /matching cho phép self-match **một CV PARSED** với JD ACTIVE/PARSED, không phải thao tác nộp CV cho HR.
- HR: chọn JD đã PARSED + các CV PARSED trong talent pool riêng. Danh sách đang lấy tối đa 100 đầu mục (giới hạn UX được hiển thị rõ).
- Trigger POST /matching/calculate trả 202/PENDING, không cam kết tính xong. Nếu response 503 do dispatcher, batch có thể đã được prepare; không tự retry để tránh làm thay generation khi chưa kiểm tra.
- GET /matching, /{id}, /{id}/gap-analysis: polling + score breakdown + skill gap; score null là chưa có, **không chuyển thành 0**. Không có LLM/XAI explanation trong MVP.
- Route SSR /matching và /matching/[id] không index; API không cache. Admin shell chưa được phép sử dụng UI F05 dù backend có quyền quản trị.
- npm test chạy unit + production HTTP mock FastAPI cùng regression F01–F04.

## Cần xác minh khi có hệ thống thật

PostgreSQL+pgvector, BGE-M3 embeddings, Celery task dispatch/recovery, generation/revision invalidation, Candidate ACTIVE-JD visibility, self-match privacy, HR ownership và batch all-or-nothing, cross-user 401/403/404, gap payload taxonomy, responsive/keyboard. Mock HTTP không thay thế kiểm thử nghiệp vụ thật.
