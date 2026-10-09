"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { ResumeDataResponse, ResumeDetail, ResumeStatusResponse } from "@/lib/resume/contracts";
import { ParsedDataEditor } from "./resume-editor";

const statusNames = { PENDING: "Đang chờ", PROCESSING: "Đang phân tích", PARSED: "Đã phân tích", FAILED: "Thất bại" } as const;

type ErrorBody = { error?: { message?: string; code?: string } };
async function errorText(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({})) as ErrorBody;
  return body.error?.message || fallback;
}

export function ResumeDetailClient({ id }: { id: string }) {
  const router = useRouter();
  const [detail, setDetail] = useState<ResumeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [working, setWorking] = useState(false);
  const [editing, setEditing] = useState(false);

  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`/api/candidate/resumes/${id}`, { cache: "no-store", signal });
      if (!response.ok) throw new Error(await errorText(response, "Không thể đọc CV."));
      const payload = await response.json() as ResumeDataResponse;
      if (!signal?.aborted) { setDetail(payload.data); setError(""); }
    } catch (cause) {
      if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải CV.");
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [id]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reload(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reload]);

  useEffect(() => {
    if (!detail || (detail.resume.parsing_status !== "PENDING" && detail.resume.parsing_status !== "PROCESSING")) return;
    const timer = window.setInterval(async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(`/api/candidate/resumes/${id}/status`, { cache: "no-store" });
        if (!response.ok) return;
        const body = await response.json() as ResumeStatusResponse;
        if (body.data.parsing_status !== detail.resume.parsing_status || body.data.revision !== detail.resume.revision) {
          setStatusMessage(body.data.error_message ?? "");
          void reload();
        }
      } catch { /* Polling best-effort; user can refresh manually. */ }
    }, 10000);
    return () => window.clearInterval(timer);
  }, [detail, id, reload]);

  async function remove() {
    if (!detail || working || !window.confirm(`Xóa CV "${detail.resume.file_name}"? Thao tác này là xóa mềm và không thể hoàn tác qua giao diện.`)) return;
    setWorking(true); setError("");
    try {
      const response = await fetch(`/api/candidate/resumes/${id}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!response.ok) throw new Error(await errorText(response, "Không thể xóa CV."));
      router.replace("/cv"); router.refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không thể xóa CV."); }
    finally { setWorking(false); }
  }

  async function download() {
    if (!detail || working) return;
    setWorking(true); setError("");
    try {
      const response = await fetch(`/api/candidate/resumes/${id}/download`, { cache: "no-store" });
      if (!response.ok) throw new Error(await errorText(response, "Không thể tải file."));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = detail.resume.file_name; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không thể tải file."); }
    finally { setWorking(false); }
  }

  if (loading) return <p role="status">Đang tải hồ sơ CV…</p>;
  if (!detail) return <div className="resume-panel"><p role="alert">{error || "Không tìm thấy hồ sơ CV."}</p><button className="button button-secondary" onClick={() => void reload()}>Thử lại</button></div>;
  const resume = detail.resume;
  return <div className="resume-workspace">
    <section className="resume-panel">
      <div className="resume-section-head">
        <div><span className="section-kicker">CHI TIẾT HỒ SƠ</span><h1 className="resume-detail-title">{resume.file_name}</h1>
          <p>Mã CV: <code>{resume.id}</code> · Phiên bản dữ liệu: {resume.revision}</p></div>
        <span className={`resume-status resume-status-${resume.parsing_status.toLowerCase()}`}>{statusNames[resume.parsing_status]}</span>
      </div>
      <div className="resume-action-row">
        <button className="button button-secondary" type="button" disabled={working} onClick={() => void download()}>Tải tệp gốc</button>
        <button className="button button-secondary" type="button" disabled={working} onClick={() => void reload()}>Làm mới</button>
        <button className="button resume-delete" type="button" disabled={working} onClick={() => void remove()}>Xóa CV</button>
      </div>
      {error && <p role="alert" className="form-error">{error}</p>}
      {statusMessage && <p className="form-error" role="status">{statusMessage}</p>}
      {(resume.parsing_status === "PENDING" || resume.parsing_status === "PROCESSING") && <p role="status" className="resume-info">Hệ thống đang phân tích CV; trạng thái được tự kiểm tra định kỳ. Không cần tải file lần nữa.</p>}
      {resume.parsing_status === "FAILED" && <p className="resume-info">Phân tích CV chưa thành công. Bạn vẫn có thể tải file gốc hoặc tải một CV khác.</p>}
    </section>

    {resume.parsing_status === "PARSED" && <section className="resume-panel">
      <div className="resume-section-head"><div><h2>Dữ liệu đã nhận diện</h2><p>Hãy kiểm tra kỹ trước khi dùng để đối chiếu với công việc.</p></div>
        <button type="button" className="button button-secondary" onClick={() => setEditing((state) => !state)}>{editing ? "Đóng chỉnh sửa" : "Hiệu chỉnh dữ liệu"}</button>
      </div>
      {editing ? <ParsedDataEditor detail={detail} id={id} onSaved={(updated) => { setDetail(updated); setEditing(false); }} /> :
        <div className="resume-detail-grid">
          <article className="resume-detail-group"><h3>Thông tin ứng viên</h3>
            {detail.candidate_profile ? <dl>{Object.entries({
              "Họ tên": detail.candidate_profile.full_name, "Email": detail.candidate_profile.email,
              "Chức danh": detail.candidate_profile.current_title, "Địa điểm": detail.candidate_profile.location,
              "Điện thoại": detail.candidate_profile.phone_number, "Giới thiệu": detail.candidate_profile.professional_summary,
            }).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "—"}</dd></div>)}</dl> : <p>Chưa có dữ liệu hồ sơ ứng viên.</p>}
          </article>
          <article className="resume-detail-group"><h3>Kỹ năng ({detail.skills.length})</h3>
            {detail.skills.length ? <ul>{detail.skills.map((skill) => <li key={skill.skill_id}>Mã kỹ năng #{skill.skill_id} · {skill.years_of_experience ?? "—"} năm · {skill.proficiency_level || "Chưa xếp mức"}</li>)}</ul> : <p>Chưa nhận diện kỹ năng.</p>}
          </article>
          <article className="resume-detail-group"><h3>Kinh nghiệm ({detail.experiences.length})</h3>
            {detail.experiences.length ? <ul>{detail.experiences.map((item, index) => <li key={index}><strong>{item.job_title}</strong> – {item.company_name}<small>{item.start_date || "?"} → {item.is_current ? "Hiện tại" : item.end_date || "?"}</small></li>)}</ul> : <p>Chưa có kinh nghiệm.</p>}
          </article>
          <article className="resume-detail-group"><h3>Học vấn ({detail.educations.length})</h3>
            {detail.educations.length ? <ul>{detail.educations.map((item, index) => <li key={index}><strong>{item.institution_name}</strong> · {item.degree || "Chưa rõ bằng cấp"}<small>{item.field_of_study || ""}</small></li>)}</ul> : <p>Chưa có học vấn.</p>}
          </article>
        </div>}
    </section>}
  </div>;
}
