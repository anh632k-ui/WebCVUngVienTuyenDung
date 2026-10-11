# Task 6C-R3A — Thiết kế an toàn cho Worker Timeout & Resource Isolation

**Trạng thái:** DESIGN REVIEW ONLY — chưa triển khai timeout, chưa sign-off bảo vệ runtime.  
**Baseline:** `dev` tại `0e411d58f1e4b2bc7f9883704da7651a106112aa`; Task 6B PASS, pypdf 6.20.0 đã được merge (Task 6C-R2).  
**Phạm vi:** Resume parse, Job parse, Match; Celery/Redis, CAS, recovery; Windows development và Linux deployment.

## 1. Bằng chứng từ mã nguồn hiện tại

| Lớp | Hiện trạng và ảnh hưởng |
| --- | --- |
| `app/tasks/celery_app.py` | Chưa khai báo `task_soft_time_limit`, `task_time_limit`, `worker_max_memory_per_child`. |
| `app/tasks/celery_tasks.py` | Task nhận resource ID + revision/generation, gọi runtime đồng bộ; không tự khôi phục trạng thái nếu child chết. |
| `app/tasks/worker_runtime.py` | `asyncio.Runner`, SQLAlchemy engine và embedding provider gắn với PID; cần bảo toàn vòng đời theo child. |
| `app/workers/resume_parse_worker.py` | Claim `PENDING -> PROCESSING` trước khi parse PDF/DOCX qua `asyncio.to_thread`, sau đó embedding và terminal CAS. |
| `app/workers/job_parse_worker.py` | Claim trước `asyncio.to_thread(parse_job_description)`, embedding và terminal CAS. |
| `app/workers/match_worker.py` | Claim `PENDING -> PROCESSING`, terminal CAS phụ thuộc generation và hai linked resources. |
| `app/services/{resume,job,match}_recovery.py` | Các sweeper **chỉ chọn PENDING** đủ cutoff; không reset PROCESSING. |
| `app/core/version_guard.py` | Claim yêu cầu đúng revision, PENDING, not deleted; terminal chỉ chấp nhận PROCESSING và snapshot hiện hành. |
| `backend/README.md` | Windows `--pool=solo`, Linux `--pool=prefork`. |

`pypdf 6.20.0` cải thiện giới hạn decoding nhưng **không đặt giới hạn CPU/RAM/thời gian của cả worker**.

## 2. Failure mode chặn việc bật Celery hard timeout trực tiếp

1. Worker claim thành công và commit `PROCESSING`.
2. PDF parser, embedding hoặc Match calculation bị treo/tốn tài nguyên.
3. Celery hard timeout/OS kill chấm dứt child mà không chạy terminal CAS `FAILED`/`PARSED`/`COMPLETED`.
4. Recovery chỉ lựa chọn `PENDING`, nên resource vẫn `PROCESSING` vô hạn; redelivery (nếu có) cũng không thể claim lại `PROCESSING` bằng điều kiện hiện tại.

**Quyết định:** Không bật hard time limit đơn độc; không tự động đổi `PROCESSING -> PENDING`, không đổi revision/generation hoặc nới điều kiện CAS để tái xử lý. Làm vậy có thể khiến worker cũ và worker mới cùng ghi kết quả nếu thiếu fencing.

`asyncio.wait_for(asyncio.to_thread(...))` chỉ có thể chấm dứt việc *đợi* của coroutine, không đảm bảo dừng thread parser. Hủy `Runner` khi thread vẫn chạy có thể kéo dài shutdown. Đây **không** phải giải pháp cô lập tài nguyên.

## 3. Thiết kế mục tiêu theo giai đoạn

### R3B — Cách ly công đoạn đọc PDF/DOCX không tin cậy

- Tách riêng **text extraction/parsing đầu vào** khỏi worker chính bằng process do ứng dụng quản lý; chạy được cả Windows và Linux; process này không sở hữu DB engine, Redis connection hoặc embedding model.
- Parent áp dụng thời hạn hữu hạn, kết thúc và thu hồi child nếu quá hạn, chờ child kết thúc và dọn file/IPC; không dùng thread timeout như biện pháp cưỡng chế.
- IPC dùng cấu trúc dữ liệu bị ràng buộc dung lượng/kiểu; không deserialize pickle từ nguồn bên ngoài, không ghi nội dung CV/secret vào logs.
- Child chỉ trả dữ liệu hoặc lỗi đã chuẩn hóa; **parent giữ quyền terminal CAS** và ghi `FAILED` nếu timeout theo đường đã có; không ghi một phần Parsed Aggregate.
- Có bound kích thước input/output, số process đồng thời và memory/CPU theo OS trong môi trường triển khai. Không ép tải BGE-M3 vào subprocess parser hoặc tạo model copy mỗi task.
- Review tác động Windows spawn, Windows antivirus/path, UTF-8 Vietnamese, DOCX metadata, unit fixture PDF và perf trước khi bật mặc định.
- Tương thích API, idempotency fingerprint, preprocessing version và format output. Không đổi model AI khi chưa có test tương đương.

### R3C — Xử lý PROCESSING bị bỏ dở khi worker chính chết

- Thiết lập policy **stale PROCESSING** được quan sát bằng `updated_at`; duration threshold chỉ là *cảnh báo* đến khi có cơ chế đảm bảo không cắt ngang tác vụ đang hợp lệ.
- Ưu tiên xử lý terminal `PROCESSING -> FAILED` bằng guarded CAS cùng revision/generation, trạng thái hiện hành, `not deleted` và linked-resource invariants; không auto-requeue thất bại cũ.
- Nếu muốn auto-reclaim/retry `PROCESSING -> PENDING`, thiết kế **persistent claim token / attempt epoch / lease fencing** và điều chỉnh terminal CAS trước, review schema/data dictionary/PTTK, database migration rồi kiểm thử race. Không dựa chỉ vào timeout hoặc updated_at.
- Không đánh dấu `FAILED` một tác vụ chỉ vì già hơn cutoff trong khi không có giới hạn task lifetime/heartbeat đáng tin cậy; false positive sẽ gây lỗi availability.
- Phân biệt `PROCESSING` có worker sống, worker đã chết, mất broker và network partition; không suy diễn các trường hợp này là tương đương.
- Lộ trình xử lý thất bại phải giữ nguyên dữ liệu terminal, không làm lộ lỗi nội bộ hoặc ảnh hưởng các fixtures QA được bảo vệ.

### R3D — Worker limits theo môi trường, chỉ sau R3B/R3C

- Linux `prefork`: thử có kiểm soát `task_soft_time_limit`/`task_time_limit` sau khi có crash/recovery policy. Dùng giới hạn container/cgroup cho hard memory bound; `worker_max_memory_per_child` chỉ recycle **sau task**, không tự dừng tác vụ đang ngốn RAM.
- Windows `solo`: không trông cậy Celery hard/soft time limits như prefork; dùng child-process isolation cho code không tin cậy và giám sát supervisor khi cần.
- Không thay đổi `acks_late` hoặc worker prefetch mà không thử các trường hợp queue redelivery và CAS. Trả message lần hai không cứu được bản ghi đang PROCESSING.
- Cấu hình timeout/limit theo loại tác vụ và điều kiện thực tế, không áp dụng chung con số giả định cho PDF CPU, BGE-M3 và Matching.
- Trước deployment cần chứng minh broker outage, worker child loss, task termination, retry và shutdown clean trên broker, DB, storage hoàn toàn cô lập.

## 4. Test matrix bắt buộc trước merge code R3B/R3C/R3D

| Kịch bản | Điều kiện PASS |
| --- | --- |
| PDF/DOCX lành tính, tiếng Việt/Unicode | Output/normalization tương thích baseline, không hồi quy. |
| PDF lỗi, file vượt size/entry/text | Lỗi có kiểm soát, không lộ dữ liệu, parent/child thu hồi sạch. |
| Parser child quá hạn (fake controlled delay, không dùng file DoS) | Child bị dừng thật, parent nhận timeout, CAS `PROCESSING -> FAILED` đúng snapshot. |
| Parent/Celery child chết sau claim | Resource được phát hiện/giải quyết theo policy R3C, không kẹt vô hạn mà không có cảnh báo. |
| Long task hợp lệ | Không đánh dấu FAILED nhầm do cutoff; embedding hoàn thành chính xác. |
| Concurrent complete vs failure sweep | Chính xác một terminal CAS thắng; không double write hoặc duplicate aggregate. |
| Revision/generation tăng, linked soft-delete | Stale worker/sweeper không ghi terminal sai phiên bản, đúng guard. |
| Resume/Job/Match recovery gốc Task 6B | Không nới `PENDING` selection hoặc giảm khả năng stale message bị discard. |
| Windows solo / Linux prefork | Kiểm thử theo platform: không suy luận support từ unit fake. |
| OS kill, Redis outage, process leak, tài nguyên | Chỉ sign-off khi có integration QA riêng; không lấy `memory://` làm bằng chứng Redis thật. |

## 5. Operational safety và giới hạn hiện tại

- Không chạy hoặc thay đổi cluster PostgreSQL canonical port 5432.
- QA test nếu cần chỉ dùng cluster `.local/pg_qa_4e394325` trên `127.0.0.1:55432`, database cô lập `webcv_task6b_clean` với `APP_ENV=test` và `TEST_DATABASE_NAME`.
- Shared QA `webcv_ungvien` có 8 Resume PENDING + 2 Job PENDING active cũ: chỉ read-only, không bật recovery/broker trên database này.
- Không FLUSH Redis, xóa dữ liệu model/cache, chạy kill worker ngoài phiên QA hoặc thao tác môi trường deployment.
- Không tự sửa schema/ORM hoặc reset dữ liệu nhằm làm xanh tests.

## 6. Gate và trạng thái

- **R3A (đây): DESIGN REVIEW COMPLETE; CODE FIX NOT IMPLEMENTED.**
- R3B chỉ được merge sau unit/parser regression, cross-platform subprocess cleanup, security tests và CI PASS.
- R3C chỉ được merge khi có bằng chứng CAS concurrency, stale/live differentiation và worker death recovery trên database cô lập; thay đổi schema phải được review riêng.
- R3D chỉ bật hard limits sau R3B/R3C, Linux live worker/broker QA và điều kiện triển khai được phê duyệt.
- Task 6C/6D deployment sign-off vẫn **PARTIAL/BLOCKED** cho đến khi staging HTTPS + runtime worker QA hoàn thành.

**Tài liệu tham chiếu:** [Celery worker time limits](https://docs.celeryq.dev/en/stable/userguide/workers.html#time-limits), [Celery concurrency pools](https://docs.celeryq.dev/en/stable/userguide/concurrency/index.html).
