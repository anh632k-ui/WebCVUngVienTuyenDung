"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { JobData, JobPage, JobParsingStatus, JobStatus } from "@/lib/jobs/contracts";

const statusName = { DRAFT: "Bản nháp", ACTIVE: "Đang mở", CLOSED: "Đã đóng" };
const parseName = { PENDING: "Chờ phân tích", PROCESSING: "Đang phân tích", PARSED: "Phân tích xong", FAILED: "Phân tích lỗi" };
type BackendError = { error?: { message?: string } };
async function messageFor(res: Response, fallback: string) {
  const body = await res.json().catch(() => ({})) as BackendError;
  return body.error?.message || fallback;
}

export function HrJobsWorkspace() {
  const router = useRouter();
  const [items, setItems] = useState<JobPage | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<JobStatus | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creationError, setCreationError] = useState("");
  const [creating, setCreating] = useState(false);
  const key = useRef<string | null>(null);
  const [draft, setDraft] = useState({ title: "", job_level: "Junior", location: "", raw_content: "" });
  function field(name: keyof typeof draft, value: string) {
    key.current = null;
    setDraft((current) => ({ ...current, [name]: value }));
  }
  const reload = useCallback(async (signal?: AbortSignal) => {
    const q = new URLSearchParams({ page: String(page), limit: "10" });
    if (search.trim()) q.set("keyword", search.trim());
    if (filter) q.set("status", filter);
    try {
      const res = await fetch(`/api/hr/jobs?${q}`, { cache: "no-store", signal });
      if (!res.ok) throw new Error(await messageFor(res, "Không thể tải danh sách JD."));
      const body = await res.json() as JobPage;
      if (!signal?.aborted) { setItems(body); setError(""); }
    } catch (cause) { if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải JD."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [page, search, filter]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reload(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reload]);
  useEffect(() => {
    if (!items?.data.some((job) => job.parsing_status === "PENDING" || job.parsing_status === "PROCESSING")) return;
    const timer = window.setInterval(() => { if (!document.hidden) void reload(); }, 12000);
    return () => window.clearInterval(timer);
  }, [items, reload]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    const idempotencyKey = key.current ?? crypto.randomUUID();
    key.current = idempotencyKey;
    setCreating(true); setCreationError("");
    try {
      const res = await fetch("/api/hr/jobs", {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ ...draft, location: draft.location.trim() || null, w_skill: 0.5, w_semantic: 0.3, w_experience: 0.2 }),
      });
      if (!res.ok) { setCreationError(await messageFor(res, "Không thể tạo JD. Retry sẽ dùng cùng Idempotency-Key.")); return; }
      const body = await res.json() as { data: JobData };
      key.current = null;
      router.push(`/hr/jobs/${body.data.id}`);
      router.refresh();
    } catch { setCreationError("Kết nối gián đoạn. Thử lại giữ nguyên Idempotency-Key."); }
    finally { setCreating(false); }
  }

  return <div className="job-workspace">
    <section className="resume-panel"><h2>Tạo JD mới</h2><p>Tài liệu JD là văn bản. Backend sẽ chuẩn hóa, lưu và phân tích bất đồng bộ trước khi xác nhận criteria.</p>
      <form className="job-create-form" onSubmit={(event) => void create(event)}>
        <div className="resume-edit-grid">
          <label>Tiêu đề công việc<input required maxLength={200} value={draft.title} onChange={(event) => field("title", event.target.value)} placeholder="Ví dụ: Backend Developer" /></label>
          <label>Cấp bậc<input required maxLength={50} value={draft.job_level} onChange={(event) => field("job_level", event.target.value)} /></label>
          <label>Địa điểm (tùy chọn)<input maxLength={150} value={draft.location} onChange={(event) => field("location", event.target.value)} /></label>
        </div>
        <label>Nội dung JD<textarea required minLength={1} value={draft.raw_content} onChange={(event) => field("raw_content", event.target.value)} placeholder="Mô tả công việc, yêu cầu kỹ năng, kinh nghiệm..." /></label>
        <p className="resume-hint">Trọng số ban đầu: Kỹ năng 50%, Ngữ nghĩa 30%, Kinh nghiệm 20%. Có thể thay đổi khi JD ở trạng thái PARSED.</p>
        {creationError && <p className="form-error" role="alert">{creationError}</p>}
        <button className="button button-primary" disabled={creating} type="submit">{creating ? "Đang tạo JD…" : "Tạo JD"}</button>
      </form>
    </section>
    <section className="resume-panel">
      <div className="resume-section-head"><div><h2>Danh sách JD</h2><p>Theo dõi trạng thái công việc và quá trình phân tích.</p></div>
        <button type="button" className="button button-secondary" onClick={() => void reload()}>Làm mới</button></div>
      <div className="resume-filters">
        <label>Tìm theo tiêu đề<input value={search} onChange={(event) => { setPage(1); setSearch(event.target.value); }} maxLength={120} /></label>
        <label>Trạng thái<select value={filter} onChange={(event) => { setPage(1); setFilter(event.target.value as JobStatus | ""); }}>
          <option value="">Tất cả</option>{(Object.keys(statusName) as JobStatus[]).map((s) => <option value={s} key={s}>{statusName[s]}</option>)}
        </select></label>
      </div>
      {loading && <p role="status">Đang tải danh sách JD…</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {!loading && !error && items?.data.length === 0 && <p className="resume-empty">Chưa có JD nào phù hợp với bộ lọc.</p>}
      {!!items?.data.length && <div className="resume-table-wrap"><table className="resume-table">
        <thead><tr><th>JD</th><th>Trạng thái</th><th>Phân tích</th><th>Revision</th><th>Chi tiết</th></tr></thead>
        <tbody>{items.data.map((job) => <tr key={job.id}>
          <td><strong>{job.title}</strong><small>{job.job_level} · {job.location || "Không ghi địa điểm"}</small></td>
          <td><span className="resume-status">{statusName[job.status]}</span></td>
          <td>{parseName[job.parsing_status as JobParsingStatus]}</td>
          <td>{job.revision}</td>
          <td><Link className="inline-link" href={`/hr/jobs/${job.id}`}>Chi tiết →</Link></td>
        </tr>)}</tbody></table></div>}
      {items && items.meta.total_pages > 1 && <div className="resume-pagination">
        <button type="button" disabled={page <= 1} onClick={() => { setLoading(true); setPage(page - 1); }}>Trước</button>
        <span>{page}/{items.meta.total_pages}</span>
        <button type="button" disabled={page >= items.meta.total_pages} onClick={() => { setLoading(true); setPage(page + 1); }}>Sau</button>
      </div>}
    </section>
  </div>;
}
