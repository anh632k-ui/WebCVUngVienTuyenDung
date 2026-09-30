# COMMIT / REVIEW POLICY

## Branching

- PTTK rebuild chỉ thực hiện trên `pttk-sync-v2`.
- Không commit trực tiếp thiết kế thử nghiệm vào `main`.
- `main` chỉ nhận PTTK sau khi audit + Pull Request review.

## Commit

Commit nên nhỏ theo nhóm thay đổi, ví dụ:

- `docs(requirements): ...`
- `docs(uml): ...`
- `docs(db): ...`
- `docs(api): ...`
- `fix(pttk): ...`
- `ci(pttk): ...`

Nếu một thay đổi ảnh hưởng route/enum/field, phải cập nhật tất cả artefact liên quan trong cùng chuỗi commit trước khi khóa PTTK.

## Pull Request

PR từ `pttk-sync-v2` -> `main` phải ghi rõ:

- phạm vi rebuild;
- các quyết định canonical;
- kết quả CI PTTK;
- database reset plan;
- các gate còn cần user thực hiện trên máy local.

Không tự merge PR. User review trước.

## Sau merge

- reset `webcv_ungvien` theo `reset_database_plan.md`;
- chạy canonical `schema.sql`;
- verify PostgreSQL runtime;
- chỉ sau đó mới viết ORM/API nghiệp vụ.

## Quy tắc thay đổi về sau

Sau khi baseline được merge, mọi thay đổi database/API/enum/business rule phải theo thứ tự:

`update PTTK -> audit traceability -> update code -> test`.
