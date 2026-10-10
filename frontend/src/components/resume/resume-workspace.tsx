"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { isResumeFile, type ParsingStatus, type ResumePage, type ResumeSummary } from "@/lib/resume/contracts";
import { RESUME_AREAS, type ResumeArea } from "@/lib/resume/areas";

const statusNames: Record<ParsingStatus, string> = {
  PENDING: "Đang chờ", PROCESSING: "Đang phân tích", PARSED: "Đã phân tích", FAILED: "Thất bại",
};
type ErrorEnvelope = { error?: { message?: string; code?: string } };

function apiError(body: ErrorEnvelope, fallback: string) {
  return body.error?.message || fallback;
}

export function ResumeWorkspace({ area = "candidate" }: { area?: ResumeArea }) {
  const config = RESUME_AREAS[area];
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState<ParsingStatus | "">("");
  const [data, setData] = useState<ResumePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploadNote, setUploadNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const key = useRef<string | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const params = new URLSearchParams({ page: String(page), limit: "10" });
    if (keyword.trim()) params.set("keyword", keyword.trim());
    if (filter) params.set("parsing_status", filter);
    try {
      const response = await fetch(`${config.api}?${params}`, { cache: "no-store", signal });
      const body = await response.json() as ResumePage & ErrorEnvelope;
      if (!response.ok) throw new Error(apiError(body, "Không thể tải danh sách CV."));
      if (!signal?.aborted) { setData(body); setError(""); }
    } catch (cause) {
      if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải CV.");
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [page, keyword, filter, config.api]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void refresh(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [refresh]);

  useEffect(() => {
    if (!data?.data.some((item) => item.parsing_status === "PENDING" || item.parsing_status === "PROCESSING")) return;
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 12000);
    return () => window.clearInterval(timer);
  }, [data, refresh]);

  function chooseFile(selected: File | null) {
    setFile(selected);
    setUploadError(""); setUploadNote("");
    key.current = selected ? crypto.randomUUID() : null;
  }

  async function upload() {
    if (!file || uploading) return;
    if (!isResumeFile(file)) { setUploadError("Chỉ chọn PDF/DOCX từ 1 byte đến 5 MB."); return; }
    const idempotencyKey = key.current ?? crypto.randomUUID();
    key.current = idempotencyKey;
    setUploading(true); setUploadError(""); setUploadNote("");
    try {
      const form = new FormData();
      form.set("file", file);
      const response = await fetch(`${config.api}`, {
        method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: form,
      });
      const body = await response.json() as ErrorEnvelope & { data?: { resume_id?: string; parsing_status?: string } };
      if (!response.ok) { setUploadError(apiError(body, "Không thể tải CV. Thử lại sẽ dùng cùng Idempotency-Key.")); return; }
      setUploadNote("CV đã được tiếp nhận. Quá trình phân tích có thể tiếp tục ở chế độ nền.");
      setFile(null); key.current = null; setPage(1); await refresh();
    } catch { setUploadError("Kết nối gián đoạn. Thử lại giữ nguyên Idempotency-Key để tránh tạo CV trùng."); }
    finally { setUploading(false); }
  }

  return <div className="resume-workspace">
    <section className="resume-panel">
      <h2>{area === "hr" ? "Thêm CV vào talent pool" : "Tải CV mới"}</h2>
      <p>Hỗ trợ PDF/DOCX, tối đa 5 MB. Ảnh scan không đảm bảo trích xuất trong MVP. {area === "hr" ? "CV do HR tải lên chỉ thuộc kho của HR hiện tại, không lấy tự động từ ứng viên." : ""}</p>
      <div className="resume-upload-row">
        <label className="resume-file-input"><span>Chọn tệp CV</span>
          <input type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(event) => chooseFile(event.currentTarget.files?.[0] ?? null)} />
        </label>
        <span className="resume-filename">{file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "Chưa chọn file"}</span>
        <button className="button button-primary" type="button" disabled={!file || uploading} onClick={() => void upload()}>
          {uploading ? "Đang tải…" : "Tải CV"}
        </button>
      </div>
      {uploadError && <p className="form-error" role="alert">{uploadError}</p>}
      {uploadNote && <p className="form-success" role="status">{uploadNote}</p>}
    </section>

    <section className="resume-panel">
      <div className="resume-section-head">
        <div><h2>{area === "hr" ? "CV trong talent pool của tôi" : "Danh sách CV"}</h2><p>{area === "hr" ? "CV do tài khoản HR này sở hữu; chọn CV đã PARSED để batch matching với JD của bạn." : "Dữ liệu thuộc tài khoản hiện tại, không tự động chia sẻ với nhà tuyển dụng."}</p></div>
        <button className="button button-secondary" type="button" onClick={() => void refresh()} disabled={loading}>Làm mới</button>
      </div>
      <form className="resume-filters" onSubmit={(event) => { event.preventDefault(); setPage(1); void refresh(); }}>
        <label>Tìm theo tên file<input value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} maxLength={120} placeholder="Tên CV…" /></label>
        <label>Trạng thái<select value={filter} onChange={(event) => { setFilter(event.target.value as ParsingStatus | ""); setPage(1); }}>
          <option value="">Tất cả</option>{(Object.keys(statusNames) as ParsingStatus[]).map((status) => <option key={status} value={status}>{statusNames[status]}</option>)}
        </select></label>
      </form>
      {loading && <p role="status">Đang tải danh sách…</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {!loading && !error && data?.data.length === 0 && <p className="resume-empty">Chưa có CV phù hợp với bộ lọc. Hãy tải lên một CV để bắt đầu.</p>}
      {!!data?.data.length && <div className="resume-table-wrap"><table className="resume-table">
        <thead><tr><th scope="col">Tên tệp</th><th scope="col">Dung lượng</th><th scope="col">Trạng thái</th><th scope="col">Ngày tạo</th><th scope="col">Chi tiết</th></tr></thead>
        <tbody>{data.data.map((item: ResumeSummary) => <tr key={item.id}>
          <td><strong>{item.file_name}</strong>{item.is_manually_edited && <small>Đã hiệu chỉnh</small>}</td>
          <td>{(item.file_size / 1024 / 1024).toFixed(2)} MB</td>
          <td><span className={`resume-status resume-status-${item.parsing_status.toLowerCase()}`}>{statusNames[item.parsing_status]}</span></td>
          <td>{new Date(item.created_at).toLocaleDateString("vi-VN")}</td>
          <td><Link className="inline-link" href={`${config.page}/${item.id}`}>Xem CV →</Link></td>
        </tr>)}</tbody>
      </table></div>}
      {data && data.meta.total_pages > 1 && <div className="resume-pagination">
        <button type="button" disabled={page <= 1} onClick={() => { setLoading(true); setPage(page - 1); }}>Trước</button>
        <span>Trang {page}/{data.meta.total_pages}</span>
        <button type="button" disabled={page >= data.meta.total_pages} onClick={() => { setLoading(true); setPage(page + 1); }}>Sau</button>
      </div>}
    </section>
  </div>;
}
