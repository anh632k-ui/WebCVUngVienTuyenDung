# THIẾT KẾ THUẬT TOÁN MATCHING — `hybrid-v1`

## 1. Mục tiêu

Tạo điểm hỗ trợ đánh giá mức độ phù hợp CV–JD theo cách có thể giải thích, kết hợp rule/taxonomy và semantic embedding. Đây không phải mô hình tự động quyết định tuyển dụng.

## 2. Tiền điều kiện

- Resume `PARSED`, chưa soft-delete, có quyền truy cập.
- JD `PARSED`, chưa soft-delete, `is_criteria_verified=true` và có ít nhất một `job_skill` hợp lệ; Candidate chỉ match JD `ACTIVE`.
- CV/JD embedding phải tồn tại, cùng `embedding_model` và cùng dimension.
- Trọng số JD hợp lệ: `w_skill + w_semantic + w_experience = 1`.

Nếu một tiền điều kiện không đạt, service trả lỗi nghiệp vụ/`422` thay vì tạo một điểm giả định.

## 3. Skill Score

Mỗi skill yêu cầu của JD có hệ số importance:

- `MANDATORY = 2.0`
- `OPTIONAL = 1.0`

Một skill được coi là matched khi `skill_id` chuẩn hóa tồn tại trong `resume_skills`.

Với tập JD skills `J`:

`SkillCoverage = sum(weight_i * matched_i) / sum(weight_i)`

Trong đó `matched_i` bằng 1 nếu CV có skill, ngược lại 0.

`SkillScore = 100 * SkillCoverage`.

Do UC13/BR-JOB-08 yêu cầu criteria đã xác minh và có ít nhất một `job_skill`, mẫu số không được rỗng trong một Match hợp lệ. Nếu dữ liệu nội bộ vi phạm invariant này, matching phải fail validation thay vì gán `SkillScore=100`.

### Breakdown HARD/SOFT

`skills.skill_kind` cho phép tính thêm Hard Skill Coverage và Soft Skill Coverage để giải thích UI. Hai breakdown này không phải cột bắt buộc trong `match_results` v1 vì có thể tính từ evidence.

### Min years theo từng skill

`job_skills.min_years_required` được dùng để đánh dấu gap chi tiết. Phiên bản `hybrid-v1` **không trừ điểm SkillScore lần thứ hai theo số năm**, tránh double-count với Experience Score. Có thể nâng cấp ở phiên bản thuật toán sau khi có benchmark.

## 4. Semantic Score

Lấy cosine similarity giữa `resume_embedding` và `job_embedding`.

`cos = cosine_similarity(resume_vec, job_vec)`

Để giữ thang điểm ổn định:

`SemanticScore = 100 * clamp(cos, 0, 1)`

Không dùng `(cos + 1) / 2` vì similarity âm không nên tự động được nâng thành điểm trung bình.

### Vai trò `rank_bm25`

Đề cương có `rank_bm25`. Trong `hybrid-v1`, BM25 được dùng như tín hiệu lexical hỗ trợ tìm kiếm/diagnostic/reranking thử nghiệm, **không đưa trực tiếp vào SemanticScore hoặc Final Score mặc định** vì raw BM25 không có thang cố định giữa các corpus.

Nếu sau benchmark hệ thống quyết định blend BM25 vào Semantic/Text Score, phải:

1. định nghĩa công thức normalization rõ ràng;
2. cập nhật tài liệu PTTK;
3. đổi `algorithm_version` (ví dụ `hybrid-v2`);
4. tính lại các kết quả cần so sánh.

## 5. Experience Score

Xác định `candidate_total_experience_years` từ các khoảng `resume_experiences`, xử lý overlap để tránh cộng trùng thời gian nếu implementation có đủ dữ liệu ngày.

Với `required = job.min_experience_years`:

- Nếu `required <= 0`: `ExperienceScore = 100`.
- Nếu `candidate >= required`: `ExperienceScore = 100`.
- Ngược lại: `ExperienceScore = 100 * candidate / required`.

Giới hạn cuối cùng trong `[0,100]`.

## 6. Overall Score

`OverallScore = w_skill * SkillScore + w_semantic * SemanticScore + w_experience * ExperienceScore`

Mặc định thiết kế:

- `w_skill = 0.50`
- `w_semantic = 0.30`
- `w_experience = 0.20`

HR có thể chỉnh nhưng tổng bắt buộc bằng 1.00.

## 7. Skill Gap

Từ `job_skills - resume_skills`:

- Missing `MANDATORY` -> `criticality = CRITICAL`.
- Missing `OPTIONAL` -> `criticality = MINOR`.

Nếu CV có skill nhưng `years_of_experience < min_years_required` và cả hai giá trị đáng tin cậy, gap được ghi là `PARTIAL`/thiếu thâm niên trong JSON evidence thay vì coi hoàn toàn không có skill.

`matched_skills` và `missing_skills` lưu snapshot JSON tại thời điểm tính để kết quả có thể giải thích ngay cả khi taxonomy/JD thay đổi sau này.

## 8. Recalculate

Khi một trong các yếu tố sau thay đổi, kết quả cũ có thể bị stale:

- criteria JD;
- weights;
- dữ liệu parsed CV được chỉnh;
- embedding model/preprocessing;
- algorithm version.

Service có thể đặt match về `PENDING`, reset scores/error và tính lại trên cùng unique pair `(job_id, resume_id)`.

## 9. LLM/XAI

LLM chỉ đọc:
- điểm thành phần;
- matched/missing skills;
- requirements đã parse;
- evidence CV đã parse.

LLM tạo text giải thích/recommendation. Không sửa bốn score trong `match_results`.

## 10. Đánh giá thực nghiệm

Nếu có ground truth đúng như kế hoạch đề cương:

- MAE giữa `OverallScore` dự đoán và điểm chuyên gia.
- Pearson correlation cho tương quan score.
- NDCG@K và Precision@K cho leaderboard.

Mọi báo cáo metric phải ghi dataset, số lượng cặp CV–JD, cách tạo ground truth và `algorithm_version`; không công bố chỉ số nếu không đủ dữ liệu đối chứng.
