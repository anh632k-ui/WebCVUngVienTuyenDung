"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { CriteriaResponse, JobCriteria, JobData, JobResponse, JobStatus } from "@/lib/jobs/contracts";

const statusLabels = { DRAFT: "Bản nháp", ACTIVE: "Đang mở", CLOSED: "Đã đóng" } as const;
const parsedLabels = { PENDING: "Đang chờ phân tích", PROCESSING: "Đang phân tích", PARSED: "Đã phân tích", FAILED: "Phân tích thất bại" } as const;
const newSkill = () => ({ skill_id: 0, importance: "MANDATORY" as const, min_years_required: 0 });

async function getError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({})) as { error?: { message?: string; code?: string } };
  return body.error?.message || fallback;
}

export function HrJobDetail({ id }: { id: string }) {
  const router = useRouter();
  const [job, setJob] = useState<JobData | null>(null);
  const [criteria, setCriteria] = useState<JobCriteria | null>(null);
  const [draft, setDraft] = useState({ title: "", job_level: "", location: "", raw_content: "" });
  const [weights, setWeights] = useState({ skill: 50, semantic: 30, experience: 20 });
  const [minYears, setMinYears] = useState(0);
  const [education, setEducation] = useState("");
  const [skills, setSkills] = useState<JobCriteria["skills"]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`/api/hr/jobs/${id}`, { cache: "no-store", signal });
      if (!response.ok) throw new Error(await getError(response, "Không thể tải JD."));
      const body = await response.json() as JobResponse;
      if (signal?.aborted) return;
      setJob(body.data);
      setDraft({ title: body.data.title, job_level: body.data.job_level, location: body.data.location || "", raw_content: body.data.raw_content });
      setWeights({ skill: body.data.w_skill * 100, semantic: body.data.w_semantic * 100, experience: body.data.w_experience * 100 });
      if (body.data.parsing_status === "PARSED") {
        const criteriaResult = await fetch(`/api/hr/jobs/${id}/criteria`, { cache: "no-store", signal });
        if (criteriaResult.ok) {
          const parsed = await criteriaResult.json() as CriteriaResponse;
          if (!signal?.aborted) {
            setCriteria(parsed.data); setMinYears(parsed.data.min_experience_years);
            setEducation(parsed.data.education_requirement ?? ""); setSkills(parsed.data.skills);
          }
        }
      }
      setError("");
    } catch (cause) { if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải JD."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [id]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reload(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reload]);

  useEffect(() => {
    if (!job || (job.parsing_status !== "PENDING" && job.parsing_status !== "PROCESSING")) return;
    const timer = window.setInterval(() => { if (!document.hidden) void reload(); }, 12000);
    return () => window.clearInterval(timer);
  }, [job, reload]);

  async function mutation(route: string, method: string, body: object, label: string) {
    if (working) return false;
    setWorking(label); setError(""); setNotice("");
    try {
      const response = await fetch(route, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.ok) throw new Error(await getError(response, `Không thể ${label.toLowerCase()}.`));
      setNotice(`${label} thành công.`);
      await reload();
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể thực hiện.");
      return false;
    } finally { setWorking(""); }
  }

  async function updateFields(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await mutation(`/api/hr/jobs/${id}`, "PUT", { ...draft, location: draft.location.trim() || null }, "Cập nhật nội dung JD");
  }

  async function updateCriteria(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (skills.length === 0 || new Set(skills.map((s) => s.skill_id)).size !== skills.length ||
      skills.some((s) => !Number.isSafeInteger(s.skill_id) || s.skill_id < 1)) {
      setError("Criteria phải có ít nhất một mã kỹ năng taxonomy hợp lệ, không trùng."); return;
    }
    await mutation(`/api/hr/jobs/${id}/criteria`, "PUT",
      { min_experience_years: minYears, education_requirement: education.trim() || null, skills }, "Xác nhận tiêu chí");
  }

  async function updateWeights(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = [weights.skill, weights.semantic, weights.experience];
    if (values.some((v) => !Number.isFinite(v) || v < 0 || v > 100 || Math.abs(v * 10 - Math.round(v * 10)) > 1e-7) ||
      Math.round(values.reduce((a, b) => a + b, 0) * 10) !== 1000) {
      setError("Trọng số phải là số từ 0–100%, tối đa một chữ số thập phân và tổng đúng 100%."); return;
    }
    if (!window.confirm("Lưu trọng số sẽ tăng revision và vô hiệu hóa kết quả matching cũ, kể cả khi giá trị không đổi. Tiếp tục?")) return;
    const recalculate = (event.currentTarget.elements.namedItem("recalculate") as HTMLInputElement | null)?.checked ?? false;
    await mutation(`/api/hr/jobs/${id}/weights`, "PUT", {
      w_skill: Math.round(weights.skill * 10) / 1000,
      w_semantic: Math.round(weights.semantic * 10) / 1000,
      w_experience: Math.round(weights.experience * 10) / 1000,
      recalculate,
    }, "Cập nhật trọng số");
  }

  async function changeStatus(status: JobStatus) {
    if (working || !window.confirm(`Chuyển JD sang "${statusLabels[status]}"? Backend sẽ kiểm tra mọi điều kiện nghiệp vụ.`)) return;
    await mutation(`/api/hr/jobs/${id}/status`, "PATCH", { status }, "Cập nhật trạng thái");
  }

  async function remove() {
    if (working || !window.confirm("Xóa mềm JD này? Kết quả matching liên quan sẽ không còn được hiển thị như hiện hành.")) return;
    setWorking("Xóa JD"); setError("");
    try {
      const response = await fetch(`/api/hr/jobs/${id}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!response.ok) throw new Error(await getError(response, "Không thể xóa JD."));
      router.replace("/hr/jobs"); router.refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không thể xóa JD."); }
    finally { setWorking(""); }
  }

  if (loading) return <p role="status">Đang tải JD…</p>;
  if (!job) return <div className="resume-panel"><p className="form-error" role="alert">{error || "Không tìm thấy JD."}</p><button className="button button-secondary" onClick={() => void reload()}>Thử lại</button></div>;
  return <div className="job-workspace">
    <section className="resume-panel">
      <div className="resume-section-head"><div><span className="section-kicker">QUẢN LÝ JD</span>
        <h1 className="resume-detail-title">{job.title}</h1><p>ID: <code>{job.id}</code> · Revision: {job.revision}</p></div>
        <span className="resume-status">{statusLabels[job.status]}</span></div>
      <p>Trạng thái phân tích: <strong>{parsedLabels[job.parsing_status]}</strong> · Criteria: <strong>{job.is_criteria_verified ? "Đã xác nhận" : "Chưa xác nhận"}</strong></p>
      {(job.parsing_status === "PENDING" || job.parsing_status === "PROCESSING") && <p className="resume-info">Backend đang xử lý JD bất đồng bộ. Các tiêu chí và trọng số chỉ chỉnh khi JD đã PARSED.</p>}
      <div className="resume-action-row">
        {(["DRAFT", "ACTIVE", "CLOSED"] as JobStatus[]).map((status) =>
          <button key={status} type="button" className="button button-secondary" disabled={!!working || status === job.status || (status === "ACTIVE" && (job.parsing_status !== "PARSED" || !job.is_criteria_verified))}
            onClick={() => void changeStatus(status)}>{statusLabels[status]}</button>)}
        <button className="button resume-delete" type="button" disabled={!!working} onClick={() => void remove()}>Xóa JD</button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-success" role="status">{notice}</p>}
    </section>

    <section className="resume-panel"><h2>Nội dung JD</h2>
      <form className="job-create-form" onSubmit={(event) => void updateFields(event)}>
        <div className="resume-edit-grid">
          <label>Tiêu đề<input required maxLength={200} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
          <label>Cấp bậc<input required maxLength={50} value={draft.job_level} onChange={(event) => setDraft({ ...draft, job_level: event.target.value })} /></label>
          <label>Địa điểm<input maxLength={150} value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} /></label>
        </div>
        <label>Nội dung<textarea required value={draft.raw_content} onChange={(event) => setDraft({ ...draft, raw_content: event.target.value })} /></label>
        <button className="button button-primary" type="submit" disabled={!!working}>{working ? "Đang xử lý…" : "Lưu JD"}</button>
        <p className="resume-hint">Cập nhật JD có thể khiến dữ liệu phân tích và matching cũ trở nên lỗi thời. Hãy đợi PARSED trước khi xác nhận criteria.</p>
      </form>
    </section>

    <section className="resume-panel"><h2>Tiêu chí JD</h2>
      <p className="resume-hint">Chỉ có thể cập nhật sau khi PARSED. Skill ID phải thuộc taxonomy hệ thống; ít nhất một kỹ năng, không trùng mã.</p>
      <form className="job-create-form" onSubmit={(event) => void updateCriteria(event)}>
        <div className="resume-edit-grid">
          <label>Yêu cầu số năm kinh nghiệm<input type="number" step="0.1" min={0} max={999.9} required value={minYears} onChange={(event) => setMinYears(Number(event.target.value))} /></label>
          <label>Học vấn<input maxLength={255} value={education} onChange={(event) => setEducation(event.target.value)} /></label>
        </div>
        {skills.map((skill, index) => <div className="resume-edit-row" key={index}>
          <label>Skill ID<input type="number" min={1} required value={skill.skill_id || ""} onChange={(event) => setSkills(skills.map((s, i) => i === index ? { ...s, skill_id: Number(event.target.value) } : s))} /></label>
          <label>Mức độ<select value={skill.importance} onChange={(event) => setSkills(skills.map((s, i) => i === index ? { ...s, importance: event.target.value as "MANDATORY" | "OPTIONAL" } : s))}>
            <option value="MANDATORY">Bắt buộc</option><option value="OPTIONAL">Tùy chọn</option></select></label>
          <label>Số năm tối thiểu<input type="number" min={0} max={999.9} step="0.1" value={skill.min_years_required} onChange={(event) => setSkills(skills.map((s, i) => i === index ? { ...s, min_years_required: Number(event.target.value) } : s))} /></label>
          <button type="button" onClick={() => setSkills(skills.filter((_, i) => i !== index))}>Bỏ</button>
        </div>)}
        <div className="resume-action-row"><button type="button" className="button button-secondary" disabled={job.parsing_status !== "PARSED"} onClick={() => setSkills([...skills, newSkill()])}>+ Thêm kỹ năng</button>
          <button className="button button-primary" type="submit" disabled={job.parsing_status !== "PARSED" || !!working}>{working ? "Đang lưu…" : "Xác nhận criteria"}</button></div>
      </form>
      {criteria && <p className="resume-hint">Bản criteria đang xem: revision {criteria.revision}.</p>}
    </section>

    <section className="resume-panel"><h2>Trọng số Hybrid Matching</h2>
      <p className="resume-hint">Mỗi lần PUT accepted, kể cả cùng giá trị, sẽ tăng revision và invalidate kết quả matching trước đó. `recalculate=true` chỉ yêu cầu best-effort dispatch sau commit; không phải cam kết matching hoàn thành ngay.</p>
      <form className="job-create-form" onSubmit={(event) => void updateWeights(event)}>
        <div className="resume-edit-grid">
          {(["skill", "semantic", "experience"] as const).map((key) => <label key={key}>{key === "skill" ? "Kỹ năng (%)" : key === "semantic" ? "Ngữ nghĩa (%)" : "Kinh nghiệm (%)"}
            <input type="number" step="0.1" min={0} max={100} value={weights[key]} onChange={(event) => setWeights({ ...weights, [key]: Number(event.target.value) })} /></label>)}
        </div>
        <p>Tổng: <strong>{(weights.skill + weights.semantic + weights.experience).toFixed(1)}%</strong></p>
        <label className="resume-checkbox"><input name="recalculate" type="checkbox" /> Yêu cầu thử tính lại matching hiện hành sau commit (best-effort)</label>
        <button className="button button-primary" disabled={job.parsing_status !== "PARSED" || !!working}>Lưu trọng số</button>
      </form>
    </section>
  </div>;
}
