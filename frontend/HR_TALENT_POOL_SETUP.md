# HR Talent Pool – Frontend MVP

HR uses `/hr/talent-pool` to upload and manage owned PDF/DOCX CVs (max 5 MiB), monitor parsing, edit parsed data, download source, and soft-delete records.

The BFF exposes `/api/hr/resumes`, `/api/hr/resumes/[id]`, `/status`, `/download`, and `/parsed-data`, with a server-side HR-only guard on every endpoint. FastAPI separately enforces `resumes.owner_user_id` for each operation. Candidate's `/api/candidate/resumes` scope remains unchanged.

There is **no candidate-to-HR CV transfer or job application** in the MVP. HR's pool contains CVs directly uploaded by the HR account. Batch matching uses existing HR-owned resumes/JDs and accepts only eligible PARSED records.

Resume UI components are reused via fixed role-specific route mappings. CSRF origin checks, UUID Idempotency-Key, HTTP 413 for oversized uploads, no-store responses, protected noindex metadata, and role redirects remain in place.

`npm test` exercises production Next.js HTTP against mock FastAPI for HR/Candidate isolation and BFF semantics; this does not demonstrate actual DB ownership or AI workers. Before merging, run live PostgreSQL/FastAPI E2E with HR A, HR B, Candidate A, and batch matching with actual HR-owned fixture CVs. Never use real personal CVs.
