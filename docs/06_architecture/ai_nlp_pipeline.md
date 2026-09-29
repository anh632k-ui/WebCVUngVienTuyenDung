# THIẾT KẾ PIPELINE AI/NLP

## 1. Nguyên tắc

Pipeline phải tạo dữ liệu có thể kiểm tra và sửa thủ công. Mô hình AI/NLP không được coi là nguồn chân lý tuyệt đối.

## 2. CV Pipeline

### Bước 1 — File validation
- PDF/DOCX, <= 5 MB.
- Kiểm MIME/magic bytes ở backend.
- Sinh `storage_key` nội bộ; không dùng trực tiếp tên file làm đường dẫn.

### Bước 2 — Text extraction
- DOCX: `python-docx`.
- PDF có text layer: `pdfplumber` hoặc PyMuPDF.
- PDF scan: OCR fallback nếu module được bật.
- Nếu không thu được text có ý nghĩa: `FAILED`, không tạo dữ liệu giả.

### Bước 3 — Preprocessing
- Unicode NFC.
- Chuẩn hóa whitespace và line breaks.
- Giữ cấu trúc dòng/section đủ để phát hiện Skills, Experience, Education.

### Bước 4 — Information Extraction
- Regex: email, phone, URL, GitHub, LinkedIn, một số date pattern.
- Section segmentation: summary/skills/experience/education.
- NER/rule-based: tên, công ty, vị trí, trường học, địa điểm.
- Parser phải trả confidence/absence ở internal representation nếu triển khai được; field không chắc chắn được phép NULL.

### Bước 5 — Skill normalization
- Chuẩn hóa candidate phrase về `skills.normalized_name`.
- Alias do ứng dụng quản lý trong taxonomy/code hoặc dataset seed.
- Không INSERT skill lạ tự động vào taxonomy production mà không có quy tắc kiểm soát; skill chưa map được có thể log để review.

### Bước 6 — Structured persistence
- `candidate_profiles`: contact/professional snapshot của CV.
- `resume_skills`.
- `resume_experiences`.
- `resume_educations`.

### Bước 7 — Embedding
- Ghép text đại diện CV từ summary + skills + experience + education có cấu trúc.
- Model ưu tiên: `bge-m3` hoặc model đa ngôn ngữ tương đương.
- PDM hiện khóa `vector(1024)`; nếu thay model có dimension khác phải thay PDM/migration trước khi code.
- Ghi `embedding_model` để tránh trộn vector khác model.

### Bước 8 — Human-in-the-loop
User xem dữ liệu đã parse và chỉnh; service đánh dấu `is_manually_edited=true`. Nếu dữ liệu có ảnh hưởng embedding, service phải tái sinh embedding sau transaction thành công.

## 3. JD Pipeline

1. Normalize raw JD.
2. Section segmentation: responsibilities, requirements, preferred/nice-to-have, benefits.
3. Trích `min_experience_years` và `education_requirement` nếu có.
4. Trích skill phrase.
5. Map Skill Taxonomy.
6. Xác định importance:
   - section Must/Required -> `MANDATORY`;
   - Preferred/Nice-to-have -> `OPTIONAL`;
   - không chắc chắn -> mặc định `OPTIONAL` để tránh phạt ứng viên quá mức, sau đó HR review.
7. Sinh `job_embedding` bằng cùng model CV.
8. Lưu `job_skills`, parsed metadata; `parsing_status=PARSED`.
9. HR rà soát criteria và đặt `is_criteria_verified=true`.

## 4. Background processing

Interface nghiệp vụ chỉ yêu cầu một Task Dispatcher. Triển khai mục tiêu có thể là Celery + Redis. Trong giai đoạn MVP/local, có thể dùng implementation đơn giản hơn nhưng API/state machine không thay đổi.

Upload/trigger endpoint trả `202 Accepted` khi công việc được nhận, trạng thái được đọc lại bằng status/result endpoint.

## 5. LLM/XAI

LLM là adapter nâng cao cho:
- diễn giải vì sao điểm cao/thấp;
- tóm tắt matched/missing skills;
- gợi ý cải thiện CV/lộ trình học.

LLM nhận **evidence đã xác định** từ Matching Engine và không được:
- tự sửa `overall_score`;
- tự thêm skill vào tập matched nếu taxonomy engine không xác nhận;
- tạo học vấn/kinh nghiệm không có trong CV.

## 6. Phiên bản và tái lập

Khi thay preprocessing/model/công thức matching, tăng `algorithm_version` hoặc model version. Kết quả thực nghiệm phải ghi rõ phiên bản để có thể tái lập.
