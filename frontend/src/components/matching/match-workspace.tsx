"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { WorkspaceRole, MatchPage, MatchSummary, MatchStatus } from "@/lib/matching/contracts";
import { safeScore } from "@/lib/matching/contracts";
import type { ResumePage } from "@/lib/resume/contracts";
import type { JobData, JobPage } from "@/lib/jobs/contracts";

const stateLabels = { PENDING: "Đang chờ", PROCESSING: "Đang tính", COMPLETED: "Hoàn thành", FAILED: "Thất bại" } as const;
type ErrorBody = { error?: { code?: string; message?: string } };
async function errorMessage(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({})) as ErrorBody;
  return body.error?.message || fallback;
}

export function MatchWorkspace({ role }: { role: WorkspaceRole }) {
  const [jobs, setJobs] = useState<JobData[]>([]);
  const [resumes, setResumes] = useState<ResumePage["data"]>([]);
  const [matches, setMatches] = useState<MatchPage | null>(null);
  const [jobId, setJobId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<MatchStatus | "">("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const reloadMatches = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ page: String(page), limit: "10" });
    if (status) query.set("status", status);
    try {
      const result = await fetch(`/api/workspace/matching?${query}`, { cache: "no-store", signal });
      if (!result.ok) throw new Error(await errorMessage(result, "Không thể tải kết quả matching."));
      const body = await result.json() as MatchPage;
      if (!signal?.aborted) { setMatches(body); setError(""); }
    } catch (cause) { if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải matching."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [page, status]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (controller.signal.aborted) return;
      void (async () => {
        try {
          const [j, r] = await Promise.all([
            fetch("/api/workspace/matching/jobs?page=1&limit=100", { cache: "no-store", signal: controller.signal }),
            fetch("/api/workspace/matching/resumes?page=1&limit=100", { cache: "no-store", signal: controller.signal }),
          ]);
          if (!j.ok || !r.ok) throw new Error("Chưa thể tải JD/CV để đối chiếu.");
          const jobData = await j.json() as JobPage, resumeData = await r.json() as ResumePage;
          if (!controller.signal.aborted) { setJobs(jobData.data); setResumes(resumeData.data); }
        } catch { if (!controller.signal.aborted) setError("Không thể tải lựa chọn JD/CV lúc này."); }
      })();
    }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reloadMatches(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reloadMatches]);

  useEffect(() => {
    if (!matches?.data.some((m) => m.status === "PENDING" || m.status === "PROCESSING")) return;
    const timer = window.setInterval(() => { if (!document.hidden) void reloadMatches(); }, 12000);
    return () => window.clearInterval(timer);
  }, [matches, reloadMatches]);

  const eligibleJobs = jobs.filter((job) => job.parsing_status === "PARSED" && (role === "HR" || job.status === "ACTIVE"));
  const eligibleResumes = resumes.filter((resume) => resume.parsing_status === "PARSED");

  async function calculate() {
    if (!jobId || selected.length === 0 || working) return;
    if (role === "CANDIDATE" && selected.length !== 1) { setError("Self-match chọn đúng một CV."); return; }
    setWorking(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/workspace/matching/calculate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, resume_ids: selected }),
      });
      if (!response.ok) {
        const msg = await errorMessage(response, "Không thể bắt đầu matching.");
        throw new Error(response.status === 503 ? `${msg} Yêu cầu có thể đã được ghi nhận; hãy kiểm tra danh sách trước khi gửi lại.` : msg);
      }
      const body = await response.json() as { data?: { total_matches?: number } };
      setNotice(`Backend đã tiếp nhận ${body.data?.total_matches ?? selected.length} matching. Đây là trạng thái chờ, không phải điểm hoàn thành.`);
      setPage(1); await reloadMatches();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không thể gửi yêu cầu matching."); }
    finally { setWorking(false); }
  }

  return <div className="job-workspace">
    <section className="resume-panel">
      <h2>{role === "CANDIDATE" ? "Tự kiểm tra mức độ phù hợp" : "Batch matching talent pool"}</h2>
      <p>Chỉ chọn CV/JD đã PARSED; backend sẽ từ chối trường hợp chưa đủ điều kiện, không thuộc quyền sở hữu hoặc đã bị xóa.</p>
      <div className="resume-filters">
        <label>JD<select value={jobId} onChange={(event) => setJobId(event.target.value)}><option value="">Chọn JD</option>
          {eligibleJobs.map((job) => <option key={job.id} value={job.id}>{job.title} · {job.job_level}</option>)}
        </select></label>
      </div>
      <fieldset className="match-select"><legend>{role === "CANDIDATE" ? "Chọn một CV của bạn" : "Chọn CV trong talent pool của bạn"}</legend>
        {!eligibleResumes.length && <p>Chưa có CV được phân tích hoàn tất để đối chiếu.</p>}
        {eligibleResumes.map((resume) => <label key={resume.id}><input type={role === "CANDIDATE" ? "radio" : "checkbox"}
          name="match-resumes" checked={selected.includes(resume.id)}
          onChange={(event) => setSelected(role === "CANDIDATE" ? [resume.id] : event.target.checked ? [...selected, resume.id] : selected.filter((id) => id !== resume.id))} />
          <span>{resume.file_name}</span></label>)}
      </fieldset>
      <button type="button" className="button button-primary" disabled={working || !jobId || !selected.length} onClick={() => void calculate()}>
        {working ? "Đang gửi…" : "Bắt đầu matching"}
      </button>
      {notice && <p className="form-success" role="status">{notice}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {resumes.length >= 100 && <p className="resume-hint">Đang hiển thị tối đa 100 CV ở trang đầu. Bộ chọn toàn bộ talent pool sẽ được mở rộng khi có pagination.</p>}
    </section>
    <section className="resume-panel">
      <div className="resume-section-head"><div><h2>Kết quả matching hiện hành</h2><p>Chỉ hiển thị kết quả thuộc phạm vi quyền hiện tại; dữ liệu lịch sử đã stale không nằm trong API này.</p></div>
        <button className="button button-secondary" type="button" onClick={() => void reloadMatches()}>Làm mới</button></div>
      <div className="resume-filters"><label>Lọc trạng thái<select value={status} onChange={(event) => { setPage(1); setStatus(event.target.value as MatchStatus | ""); }}>
        <option value="">Tất cả</option>{(Object.keys(stateLabels) as MatchStatus[]).map((value) => <option value={value} key={value}>{stateLabels[value]}</option>)}
      </select></label></div>
      {loading && <p role="status">Đang tải…</p>}
      {!loading && !error && matches?.data.length === 0 && <p className="resume-empty">Chưa có kết quả matching.</p>}
      {!!matches?.data.length && <div className="resume-table-wrap"><table className="resume-table">
        <thead><tr><th>Matching</th><th>Trạng thái</th><th>Điểm tổng</th><th>Phiên</th><th>Chi tiết</th></tr></thead>
        <tbody>{matches.data.map((m: MatchSummary) => <tr key={m.id}>
          <td><strong>CV #{m.resume_id.slice(0, 8)}</strong><small>JD #{m.job_id.slice(0, 8)}</small></td>
          <td><span className={`resume-status resume-status-${m.status === "COMPLETED" ? "parsed" : m.status === "FAILED" ? "failed" : "processing"}`}>{stateLabels[m.status]}</span></td>
          <td><strong>{safeScore(m.overall_score) !== null ? `${m.overall_score?.toFixed(1)}/100` : "Chưa có"}</strong></td>
          <td>Generation {m.generation}</td>
          <td><Link className="inline-link" href={`/matching/${m.id}`}>Xem →</Link></td>
        </tr>)}</tbody></table></div>}
      {matches && matches.meta.total_pages > 1 && <div className="resume-pagination">
        <button disabled={page <= 1} onClick={() => { setLoading(true); setPage(page - 1); }}>Trước</button>
        <span>{page}/{matches.meta.total_pages}</span>
        <button disabled={page >= matches.meta.total_pages} onClick={() => { setLoading(true); setPage(page + 1); }}>Sau</button>
      </div>}
    </section>
  </div>;
}
