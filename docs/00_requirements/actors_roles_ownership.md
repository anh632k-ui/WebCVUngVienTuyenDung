# ACTOR, VAI TRÒ VÀ QUYỀN SỞ HỮU

## Guest
Đăng ký Candidate/HR và đăng nhập.

## Candidate
- Quản lý account.
- Upload/quản lý CV của mình.
- Xem JD ACTIVE.
- Self-match CV của mình với JD ACTIVE.
- Xem Match/Skill Gap của CV mình.
- Không xem leaderboard của người khác.

## HR
- Quản lý account.
- Quản lý JD của mình.
- Upload/quản lý CV trong talent pool của chính HR.
- Match JD của mình với CV thuộc kho HR.
- Xem Match/Gap/Leaderboard chỉ khi **JD và CV đều thuộc HR**.

Candidate self-match với JD của HR không tạo quyền đọc cho HR.

## Admin
- Xem/khóa/mở khóa account.
- Có quyền giám sát resource.
- Không tự khóa hoặc tự hạ quyền chính mình.

### Quy tắc đổi role
Do `resumes.owner_user_id` không lưu provenance Candidate-vs-HR riêng, đổi role có thể làm đổi nghĩa kho dữ liệu. Vì vậy MVP chỉ cho đổi `CANDIDATE <-> HR` khi user:
1. không sở hữu Resume chưa soft-delete; và
2. không sở hữu JD chưa soft-delete.

Nếu còn resource, trả `409 ROLE_CHANGE_CONFLICT`; Admin phải yêu cầu user cleanup/soft-delete trước. Không tự động reinterpret/reassign resource.

## Ownership
### Resume
`resumes.owner_user_id`.
Candidate/HR chỉ truy cập CV có owner bằng current user; Admin override.

### JD
`job_descriptions.recruiter_id`.
Chỉ **HR** tạo JD mới trong MVP, vì recruiter_id phải luôn mang nghĩa nhà tuyển dụng. Admin không dùng `POST /jobs` để tạo JD dưới tên mình; Admin chỉ giám sát/quản trị JD đã tồn tại.

### Match
- Candidate: resume thuộc Candidate; JD ACTIVE.
- HR: JD thuộc HR và resume thuộc kho HR.
- Admin: vận hành/giám sát.
- Đọc Match của HR cũng phải thỏa đồng thời JD-owner + CV-owner.

## Candidate profile cardinality
Một Resume có **0..1 `candidate_profiles`**:
- PENDING/PROCESSING/FAILED có thể chưa có profile.
- PARSED thường có snapshot.
DB enforce `UNIQUE(resume_id)` nhưng không bắt mọi Resume phải có row profile.

## BOLA/IDOR
Authorization ở backend service/dependency. Có thể trả 404 thay vì 403 khi policy không cho lộ resource.
