```markdown
# TÀI LIỆU HỢP ĐỒNG GIAO TIẾP API (API CONTRACT SPECIFICATION)

- **Hệ thống**: Tuyển dụng thông minh, Bóc tách CV và So khớp JD bằng AI/NLP
- **Phiên bản API**: v1
- **Giao thức**: RESTful API / JSON
- **Quy chuẩn mã hóa**: UTF-8

---

## MODULE 1: AUTHENTICATION & USER MANAGEMENT (PHÂN HỆ 1)

### 1.1. Đăng ký tài khoản người dùng mới
- **Endpoint**: `POST /api/v1/auth/register`
- **Quyền truy cập**: Public (Khách vãng lai)
- **Request Body (application/json)**:
```json
{
  "email": "candidate@example.com",
  "password": "SecurePassword123@",
  "full_name": "Bùi Thế Anh",
  "phone_number": "0912345678",
  "role": "CANDIDATE"
}

```

* **Validation Rules**:
* `email`: Định dạng RFC 5322, duy nhất trong hệ thống.
* `password`: Tối thiểu 8 ký tự, gồm ít nhất 1 chữ hoa, 1 chữ thường, 1 số, 1 ký tự đặc biệt.
* `role`: Thuộc enum `["CANDIDATE", "HR"]`.


* **Responses**:
* `201 Created`:


```json
{
  "success": true,
  "message": "Đăng ký tài khoản thành công",
  "data": {
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "email": "candidate@example.com",
    "full_name": "Bùi Thế Anh",
    "role": "CANDIDATE",
    "created_at": "2026-09-30T00:00:00Z"
  }
}

```


* `409 Conflict`: Email đã tồn tại.
* `422 Unprocessable Entity`: Dữ liệu không đạt chuẩn validation.



---

### 1.2. Đăng nhập hệ thống (Xác thực JWT)

* **Endpoint**: `POST /api/v1/auth/login`
* **Quyền truy cập**: Public
* **Request Body**:

```json
{
  "email": "candidate@example.com",
  "password": "SecurePassword123@"
}

```

* **Responses**:
* `200 OK`:


```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
    "token_type": "Bearer",
    "expires_in": 900,
    "user": {
      "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
      "email": "candidate@example.com",
      "full_name": "Bùi Thế Anh",
      "role": "CANDIDATE"
    }
  }
}

```


* `401 Unauthorized`: Email hoặc mật khẩu không chính xác.



---

### 1.3. Cấp mới Access Token (Refresh Token Rotation)

* **Endpoint**: `POST /api/v1/auth/refresh`
* **Quyền truy cập**: Client gửi kèm Refresh Token qua HttpOnly Cookie.
* **Responses**:
* `200 OK`:


```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsI...",
    "token_type": "Bearer",
    "expires_in": 900
  }
}

```


* `401 Unauthorized`: Token hết hạn hoặc không hợp lệ.



---

### 1.4. Quản lý thông tin tài khoản cá nhân

* **Endpoint**:
* `GET /api/v1/users/me` (Lấy thông tin cá nhân)
* `PUT /api/v1/users/me` (Cập nhật thông tin cá nhân)


* **Header**: `Authorization: Bearer <access_token>`
* **Request Body (cho PUT)**:

```json
{
  "full_name": "Bùi Thế Anh",
  "phone_number": "0987654321"
}

```

* **Responses**:
* `200 OK`: Trả về dữ liệu profile mới nhất của user.



---

## MODULE 2: RESUME & NLP PARSING (PHÂN HỆ 2)

### 2.1. Tải lên tệp CV (Upload CV)

* **Endpoint**: `POST /api/v1/resumes/upload`
* **Quyền truy cập**: Authenticated (Candidate, HR)
* **Header**: `Content-Type: multipart/form-data`
* **Form Data**:
* `file`: File nhị phân (.pdf hoặc .docx, dung lượng $\le$ 5MB).


* **Responses**:
* `202 Accepted`:


```json
{
  "success": true,
  "message": "Tải lên thành công, đang đưa vào hàng đợi phân tích",
  "data": {
    "resume_id": "e4a7a8f1-9b62-4f32-8418-2ad19cb91e3b",
    "file_name": "bui_the_anh_cv.pdf",
    "file_size": 154200,
    "parsing_status": "PENDING"
  }
}

```


* `400 Bad Request`: File không đúng định dạng.
* `413 Payload Too Large`: Dung lượng vượt quá 5MB.



---

### 2.2. Kiểm tra tiến trình phân tích CV (Polling Status)

* **Endpoint**: `GET /api/v1/resumes/{id}/status`
* **Quyền truy cập**: Chủ sở hữu CV hoặc HR/Admin.
* **Responses**:
* `200 OK`:


```json
{
  "success": true,
  "data": {
    "resume_id": "e4a7a8f1-9b62-4f32-8418-2ad19cb91e3b",
    "parsing_status": "PARSED",
    "parsed_at": "2026-09-30T00:05:00Z",
    "error_message": null
  }
}

```



---

### 2.3. Lấy dữ liệu chi tiết đã bóc tách từ CV

* **Endpoint**: `GET /api/v1/resumes/{id}`
* **Responses**:
* `200 OK`:


```json
{
  "success": true,
  "data": {
    "resume_id": "e4a7a8f1-9b62-4f32-8418-2ad19cb91e3b",
    "file_name": "bui_the_anh_cv.pdf",
    "parsing_status": "PARSED",
    "skills": [
      {
        "skill_id": 1,
        "name": "Python",
        "years_of_experience": 2.0,
        "proficiency_level": "Intermediate"
      },
      {
        "skill_id": 2,
        "name": "PostgreSQL",
        "years_of_experience": 1.5,
        "proficiency_level": "Intermediate"
      }
    ],
    "experiences": [
      {
        "company_name": "Tech Corp",
        "job_title": "Backend Intern",
        "start_date": "2025-06-01",
        "end_date": "2025-12-31",
        "is_current": false,
        "description": "Phát triển RESTful API bằng FastAPI và tối ưu SQL."
      }
    ],
    "educations": [
      {
        "institution_name": "Đại học Tài nguyên và Môi trường TP.HCM",
        "degree": "Kỹ sư",
        "field_of_study": "Công nghệ thông tin",
        "graduation_year": 2026,
        "gpa": 3.45
      }
    ]
  }
}

```



---

### 2.4. Rà soát & Cập nhật dữ liệu bóc tách (Human-in-the-loop)

* **Endpoint**: `PUT /api/v1/resumes/{id}/parsed-data`
* **Request Body**: Cấu trúc JSON tương tự dữ liệu trả về của `GET /resumes/{id}`, chứa thông tin đã được người dùng chỉnh sửa.
* **Responses**:
* `200 OK`: Xác nhận cập nhật thành công.



---

## MODULE 3: JOB DESCRIPTION MANAGEMENT (PHÂN HỆ 3)

### 3.1. Tạo tin tuyển dụng mới (HR Create JD)

* **Endpoint**: `POST /api/v1/jobs`
* **Quyền truy cập**: HR, Admin
* **Request Body**:

```json
{
  "title": "Chuyên viên Lập trình Backend (Python/FastAPI)",
  "job_level": "Junior",
  "location": "Hồ Chí Minh",
  "raw_content": "Mô tả công việc: Xây dựng hệ thống RESTful API...\nYêu cầu: Tối thiểu 1 năm Python, nắm vững FastAPI, PostgreSQL. Ưu tiên biết Docker.",
  "min_experience_years": 1.0,
  "w_skill": 0.50,
  "w_semantic": 0.30,
  "w_experience": 0.20
}

```

* **Validation**:
* Ràng buộc: `w_skill + w_semantic + w_experience == 1.0`.


* **Responses**:
* `201 Created`: Trả về `job_id` và trạng thái `ACTIVE`.



---

### 3.2. Lấy danh sách tin tuyển dụng

* **Endpoint**: `GET /api/v1/jobs`
* **Query Params**: `page=1`, `limit=10`, `status=ACTIVE`, `keyword=Python`
* **Responses**:
* `200 OK`: Danh sách các JD kèm metadata phân trang (`total_items`, `total_pages`).



---

### 3.3. Tinh chỉnh tiêu chí kỹ năng của JD

* **Endpoint**: `PUT /api/v1/jobs/{id}/criteria`
* **Request Body**:

```json
{
  "min_experience_years": 1.0,
  "skills": [
    { "skill_id": 1, "skill_type": "MANDATORY", "min_years_required": 1.0 },
    { "skill_id": 2, "skill_type": "MANDATORY", "min_years_required": 1.0 },
    { "skill_id": 15, "skill_type": "OPTIONAL", "min_years_required": 0.0 }
  ]
}

```

* **Responses**:
* `200 OK`: Cập nhật bộ tiêu chuẩn kỹ năng thành công.



---

## MODULE 4: MATCHING & ANALYTICS (PHÂN HỆ 4)

### 4.1. Kích hoạt so khớp hồ sơ với JD

* **Endpoint**: `POST /api/v1/matching/calculate`
* **Quyền truy cập**: Authenticated (Candidate, HR)
* **Request Body**:

```json
{
  "job_id": "a3b4c5d6-e7f8-9012-3456-789abcdef012",
  "resume_ids": [
    "e4a7a8f1-9b62-4f32-8418-2ad19cb91e3b"
  ]
}

```

* **Responses**:
* `202 Accepted`: Tiếp nhận và đưa tác vụ chấm điểm vào hàng đợi.



---

### 4.2. Bảng xếp hạng ứng viên theo JD (HR Leaderboard)

* **Endpoint**: `GET /api/v1/jobs/{id}/leaderboard`
* **Query Params**: `limit=50`, `min_score=60`
* **Responses**:
* `200 OK`:


```json
{
  "success": true,
  "data": {
    "job_title": "Chuyên viên Lập trình Backend (Python/FastAPI)",
    "total_candidates": 15,
    "rankings": [
      {
        "rank": 1,
        "match_id": "f1234567-89ab-cdef-0123-456789abcdef",
        "resume_id": "e4a7a8f1-9b62-4f32-8418-2ad19cb91e3b",
        "candidate_name": "Bùi Thế Anh",
        "overall_score": 88.50,
        "skill_score": 90.00,
        "semantic_score": 85.00,
        "experience_score": 90.00,
        "calculated_at": "2026-09-30T00:05:00Z"
      }
    ]
  }
}

```



---

### 4.3. Báo cáo phân tích khoảng cách kỹ năng (Skill Gap Analysis)

* **Endpoint**: `GET /api/v1/matching/{match_id}/gap-analysis`
* **Responses**:
* `200 OK`:


```json
{
  "success": true,
  "data": {
    "match_id": "f1234567-89ab-cdef-0123-456789abcdef",
    "scores": {
      "overall": 88.50,
      "skill": 90.00,
      "semantic": 85.00,
      "experience": 90.00
    },
    "matched_skills": [
      { "name": "Python", "category": "Language", "type": "MANDATORY" },
      { "name": "PostgreSQL", "category": "Database", "type": "MANDATORY" }
    ],
    "missing_skills": [
      {
        "name": "Docker",
        "category": "DevOps",
        "type": "OPTIONAL",
        "criticality": "LOW",
        "learning_recommendation": "Tìm hiểu Dockerfile cơ bản và chạy docker-compose với FastAPI & PostgreSQL."
      }
    ],
    "gap_summary": "Ứng viên đáp ứng đầy đủ kỹ năng bắt buộc. Cần bổ sung thêm kỹ năng DevOps cơ bản."
  }
}

```



```
