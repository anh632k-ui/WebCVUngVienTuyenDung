"use client";

import { useCallback, useEffect, useState } from "react";
import { safeScore, skillLabel, type GapAnalysis, type GapResponse, type MatchDetail, type MatchResponse } from "@/lib/matching/contracts";

const states = { PENDING: "Đang chờ", PROCESSING: "Đang tính toán", COMPLETED: "Đã tính xong", FAILED: "Thất bại" } as const;
function Score({ title, score }: { title: string; score: number | null }) {
  const value = safeScore(score);
  return <div className="match-score"><div><span>{title}</span><strong>{value === null ? "Chưa có" : `${value.toFixed(1)}/100`}</strong></div>
    <div className="match-track" aria-label={`${title}: ${value === null ? "chưa có điểm" : value + "/100"}`}>
      <span style={{ width: `${value ?? 0}%` }} /></div></div>;
}
async function message(res: Response) { const body = await res.json().catch(() => ({})) as { error?: { message?: string } }; return body.error?.message || "Không thể đọc kết quả."; }

export function MatchDetailClient({ id }: { id: string }) {
  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [gap, setGap] = useState<GapAnalysis | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetch(`/api/workspace/matching/${id}`, { cache: "no-store", signal });
      if (!res.ok) throw new Error(await message(res));
      const data = (await res.json() as MatchResponse).data;
      if (signal?.aborted) return;
      setMatch(data); setError("");
      if (data.status === "COMPLETED") {
        const result = await fetch(`/api/workspace/matching/${id}/gap-analysis`, { cache: "no-store", signal });
        if (result.ok) { const body = await result.json() as GapResponse; if (!signal?.aborted) setGap(body.data); }
      } else { setGap(null); }
    } catch (cause) { if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể đọc matching."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [id]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reload(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reload]);
  useEffect(() => {
    if (!match || (match.status !== "PENDING" && match.status !== "PROCESSING")) return;
    const timer = window.setInterval(() => { if (!document.hidden) void reload(); }, 12000);
    return () => window.clearInterval(timer);
  }, [match, reload]);
  if (loading) return <p role="status">Đang tải kết quả…</p>;
  if (!match) return <div className="resume-panel"><p role="alert" className="form-error">{error || "Không tìm thấy matching."}</p><button onClick={() => void reload()} className="button button-secondary">Thử lại</button></div>;
  return <div className="job-workspace">
    <section className="resume-panel">
      <div className="resume-section-head"><div><span className="section-kicker">MATCHING HIỆN HÀNH</span><h1 className="resume-detail-title">Kết quả đối chiếu CV và JD</h1>
        <p>Mã kết quả: <code>{match.id}</code> · Generation {match.generation} · Algorithm {match.algorithm_version}</p></div>
        <span className="resume-status">{states[match.status]}</span></div>
      <p>JD Revision: {match.job_revision} · CV Revision: {match.resume_revision}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      {match.status === "FAILED" && <p className="form-error" role="alert">Phân tích thất bại: {match.error_message || "Không có mô tả lỗi."}</p>}
      {(match.status === "PENDING" || match.status === "PROCESSING") && <p className="resume-info" role="status">Backend đang xử lý. Điểm chưa có không được coi là 0. Trạng thái sẽ tự cập nhật.</p>}
      <button className="button button-secondary" type="button" onClick={() => void reload()}>Làm mới</button>
    </section>
    <section className="resume-panel"><h2>Điểm Hybrid-v1</h2><p>Điểm số là tham khảo, không phải xác suất trúng tuyển hoặc quyết định tự động.</p>
      <div className="match-breakdown">
        <Score title="Điểm tổng hợp" score={match.overall_score} />
        <Score title="Kỹ năng" score={match.skill_score} />
        <Score title="Tương đồng ngữ nghĩa" score={match.semantic_score} />
        <Score title="Kinh nghiệm" score={match.experience_score} />
      </div>
      <p className="resume-hint">Mô hình embedding: {match.embedding_model || "Không có"}; preprocessing: {match.embedding_preprocessing_version || "Không có"}.</p>
    </section>
    {match.status === "COMPLETED" && <section className="resume-panel"><h2>Skill Gap và nhận xét</h2>
      {gap ? <div className="resume-detail-grid">
        <div className="resume-detail-group"><h3>Kỹ năng phù hợp ({gap.matched_skills.length})</h3>
          {gap.matched_skills.length ? <ul>{gap.matched_skills.map((skill, index) => <li key={index}>{skillLabel(skill)}</li>)}</ul> : <p>Không có kỹ năng phù hợp trong kết quả hiện tại.</p>}</div>
        <div className="resume-detail-group"><h3>Kỹ năng còn thiếu ({gap.missing_skills.length})</h3>
          {gap.missing_skills.length ? <ul>{gap.missing_skills.map((skill, index) => <li key={index}>{skillLabel(skill)}</li>)}</ul> : <p>Không có kỹ năng còn thiếu trong kết quả hiện tại.</p>}</div>
        <div className="resume-detail-group match-summary"><h3>Nhận xét</h3><p>{gap.recommendation || "Chưa có tổng hợp Skill Gap."}</p>
          <p className="resume-hint">Chưa có giải thích LLM/XAI trong MVP.</p></div>
      </div> : <p>Đang tải dữ liệu Skill Gap; vui lòng thử làm mới.</p>}
    </section>}
  </div>;
}
