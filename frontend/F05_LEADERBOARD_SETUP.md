# F05 – Leaderboard của HR

Giao diện /hr/jobs/[id]/leaderboard và BFF GET /api/hr/jobs/[id]/leaderboard gọi đúng FastAPI GET /api/v1/jobs/{id}/leaderboard với page, limit, min_score (0–100). Server xác minh phiên và role HR, FastAPI kiểm JD owner và CV thuộc talent pool.

Backend chỉ trả các Match COMPLETED hiện hành. Rank và score sử dụng nguyên dữ liệu API, không xếp lại bằng client, không có chế độ xem CV self-match riêng tư của Candidate. Không có "nộp CV cho HR" ở MVP.

Kiểm thử: unit validation + production HTTP mock integration trong npm test. Cần Codex chạy thực tế nhiều HR/Candidate/PostgreSQL/worker để xác minh ownership, stale/deleted match filtering, rank pagination và hiệu năng trên talent pool thật.
