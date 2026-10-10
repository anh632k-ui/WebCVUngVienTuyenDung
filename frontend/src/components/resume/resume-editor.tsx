"use client";

import { useState, type FormEvent } from "react";
import type { CandidateProfile, Education, Experience, ParsedDataUpdate, ResumeDataResponse, ResumeDetail, ResumeSkill } from "@/lib/resume/contracts";

const blankSkill = (): ResumeSkill => ({ skill_id: 0, years_of_experience: null, proficiency_level: null });
const blankExperience = (): Experience => ({ company_name: "", job_title: "", start_date: null, end_date: null, is_current: false, description: null });
const blankEducation = (): Education => ({ institution_name: "", degree: null, field_of_study: null, start_year: null, graduation_year: null, gpa: null, description: null });
const profileFields: { name: keyof CandidateProfile; label: string; max: number }[] = [
  { name: "full_name", label: "Họ và tên", max: 150 }, { name: "email", label: "Email", max: 255 },
  { name: "phone_number", label: "Điện thoại", max: 30 }, { name: "current_title", label: "Chức danh hiện tại", max: 150 },
  { name: "location", label: "Địa điểm", max: 150 }, { name: "linkedin_url", label: "LinkedIn URL", max: 500 },
  { name: "github_url", label: "GitHub URL", max: 500 }, { name: "professional_summary", label: "Giới thiệu chuyên môn", max: 10000 },
];

function nullableString(text: string) { return text.trim() || null; }
function nullableNumber(text: string) { return text.trim() === "" ? null : Number(text); }

export function ParsedDataEditor({ detail, id, apiBase = "/api/candidate/resumes", onSaved }: { detail: ResumeDetail; id: string; apiBase?: string; onSaved: (detail: ResumeDetail) => void }) {
  const [profile, setProfile] = useState<CandidateProfile | null>(detail.candidate_profile);
  const [skills, setSkills] = useState<ResumeSkill[]>(detail.skills);
  const [experiences, setExperiences] = useState<Experience[]>(detail.experiences);
  const [educations, setEducations] = useState<Education[]>(detail.educations);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function updateList<T>(items: T[], index: number, value: Partial<T>, setter: (items: T[]) => void) {
    setter(items.map((item, i) => i === index ? { ...item, ...value } : item));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setError("");
    const skillIds = skills.map((skill) => skill.skill_id);
    if (skillIds.some((id) => !Number.isSafeInteger(id) || id < 1) || new Set(skillIds).size !== skillIds.length) {
      setError("Mã kỹ năng phải là số nguyên dương, không trùng nhau. Hãy dùng mã kỹ năng trong taxonomy."); return;
    }
    const invalidExperience = experiences.some((item) => item.start_date && item.end_date && item.end_date < item.start_date);
    const invalidEducation = educations.some((item) => item.start_year !== null && item.graduation_year !== null && item.graduation_year < item.start_year);
    if (invalidExperience || invalidEducation) { setError("Mốc thời gian bắt đầu/kết thúc chưa hợp lệ."); return; }
    const payload: ParsedDataUpdate = { candidate_profile: profile, skills, experiences, educations };
    setSaving(true);
    try {
      const response = await fetch(`${apiBase}/${id}/parsed-data`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const body = await response.json() as ResumeDataResponse & { error?: { message?: string } };
      if (!response.ok) throw new Error(body.error?.message || "Không thể cập nhật dữ liệu trích xuất.");
      onSaved(body.data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không thể cập nhật dữ liệu."); }
    finally { setSaving(false); }
  }

  return <form className="resume-editor" onSubmit={(event) => void submit(event)}>
    <fieldset><legend>Thông tin ứng viên</legend>
      <label className="resume-checkbox"><input type="checkbox" checked={profile !== null} onChange={(event) => setProfile(event.target.checked ? { full_name: null, email: null, phone_number: null, current_title: null, location: null, linkedin_url: null, github_url: null, professional_summary: null } : null)} /> Có dữ liệu ứng viên</label>
      {profile && <div className="resume-edit-grid">{profileFields.map(({ name, label, max }) =>
        <label key={name}>{label}<input type={name === "email" ? "email" : "text"} maxLength={max} value={profile[name] ?? ""} onChange={(event) => setProfile({ ...profile, [name]: nullableString(event.target.value) })} /></label>)}</div>}
    </fieldset>
    <fieldset><legend>Kỹ năng</legend>
      <p className="resume-hint">Mã kỹ năng phải tồn tại trong taxonomy của hệ thống. Việc bổ sung trình chọn kỹ năng theo tên sẽ được hoàn thiện cùng module taxonomy.</p>
      {skills.map((skill, index) => <div className="resume-edit-row" key={index}>
        <label>Mã kỹ năng<input aria-label="Mã kỹ năng" type="number" min={1} required value={skill.skill_id || ""} onChange={(event) => updateList(skills, index, { skill_id: Number(event.target.value) }, setSkills)} /></label>
        <label>Số năm<input aria-label="Số năm kinh nghiệm" type="number" step="0.1" min={0} max={999.9} value={skill.years_of_experience ?? ""} onChange={(event) => updateList(skills, index, { years_of_experience: nullableNumber(event.target.value) }, setSkills)} /></label>
        <label>Trình độ<input aria-label="Trình độ kỹ năng" maxLength={30} value={skill.proficiency_level ?? ""} onChange={(event) => updateList(skills, index, { proficiency_level: nullableString(event.target.value) }, setSkills)} /></label>
        <button type="button" onClick={() => setSkills(skills.filter((_, i) => i !== index))}>Bỏ</button>
      </div>)}
      <button className="button button-secondary" type="button" onClick={() => setSkills([...skills, blankSkill()])}>+ Thêm kỹ năng</button>
    </fieldset>
    <fieldset><legend>Kinh nghiệm</legend>
      {experiences.map((item, index) => <div className="resume-edit-block" key={index}>
        <div className="resume-edit-grid">
          <label>Công ty<input maxLength={150} value={item.company_name} onChange={(event) => updateList(experiences, index, { company_name: event.target.value }, setExperiences)} /></label>
          <label>Vị trí<input maxLength={150} value={item.job_title} onChange={(event) => updateList(experiences, index, { job_title: event.target.value }, setExperiences)} /></label>
          <label>Ngày bắt đầu<input type="date" value={item.start_date ?? ""} onChange={(event) => updateList(experiences, index, { start_date: nullableString(event.target.value) }, setExperiences)} /></label>
          <label>Ngày kết thúc<input type="date" disabled={item.is_current} value={item.end_date ?? ""} onChange={(event) => updateList(experiences, index, { end_date: nullableString(event.target.value) }, setExperiences)} /></label>
        </div>
        <label className="resume-checkbox"><input type="checkbox" checked={item.is_current} onChange={(event) => updateList(experiences, index, { is_current: event.target.checked, end_date: event.target.checked ? null : item.end_date }, setExperiences)} /> Đang làm tại đây</label>
        <label>Mô tả<textarea value={item.description ?? ""} onChange={(event) => updateList(experiences, index, { description: nullableString(event.target.value) }, setExperiences)} /></label>
        <button type="button" onClick={() => setExperiences(experiences.filter((_, i) => i !== index))}>Bỏ kinh nghiệm</button>
      </div>)}
      <button className="button button-secondary" type="button" onClick={() => setExperiences([...experiences, blankExperience()])}>+ Thêm kinh nghiệm</button>
    </fieldset>
    <fieldset><legend>Học vấn</legend>
      {educations.map((item, index) => <div className="resume-edit-block" key={index}>
        <div className="resume-edit-grid">
          <label>Cơ sở đào tạo<input maxLength={150} value={item.institution_name} onChange={(event) => updateList(educations, index, { institution_name: event.target.value }, setEducations)} /></label>
          <label>Bằng cấp<input maxLength={100} value={item.degree ?? ""} onChange={(event) => updateList(educations, index, { degree: nullableString(event.target.value) }, setEducations)} /></label>
          <label>Chuyên ngành<input maxLength={150} value={item.field_of_study ?? ""} onChange={(event) => updateList(educations, index, { field_of_study: nullableString(event.target.value) }, setEducations)} /></label>
          <label>Năm bắt đầu<input type="number" value={item.start_year ?? ""} onChange={(event) => updateList(educations, index, { start_year: nullableNumber(event.target.value) }, setEducations)} /></label>
          <label>Năm tốt nghiệp<input type="number" value={item.graduation_year ?? ""} onChange={(event) => updateList(educations, index, { graduation_year: nullableNumber(event.target.value) }, setEducations)} /></label>
          <label>GPA<input type="number" step="0.01" value={item.gpa ?? ""} onChange={(event) => updateList(educations, index, { gpa: nullableNumber(event.target.value) }, setEducations)} /></label>
        </div>
        <label>Mô tả<textarea value={item.description ?? ""} onChange={(event) => updateList(educations, index, { description: nullableString(event.target.value) }, setEducations)} /></label>
        <button type="button" onClick={() => setEducations(educations.filter((_, i) => i !== index))}>Bỏ học vấn</button>
      </div>)}
      <button className="button button-secondary" type="button" onClick={() => setEducations([...educations, blankEducation()])}>+ Thêm học vấn</button>
    </fieldset>
    {error && <p className="form-error" role="alert">{error}</p>}
    <button type="submit" className="button button-primary" disabled={saving}>{saving ? "Đang lưu…" : "Lưu dữ liệu hiệu chỉnh"}</button>
  </form>;
}
