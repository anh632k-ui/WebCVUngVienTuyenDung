"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { LeaderboardPage } from "@/lib/leaderboard/contracts";
import { safeScore } from "@/lib/matching/contracts";

export function LeaderboardClient({ jobId }: { jobId: string }) {
  const [page, setPage] = useState(1);
  const [minimum, setMinimum] = useState("");
  const [items, setItems] = useState<LeaderboardPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const reload = useCallback(async (signal?: AbortSignal) => {
    const q = new URLSearchParams({ page: String(page), limit: "20" });
    if (minimum !== "") q.set("min_score", minimum);
    try {
      const response = await fetch(`/api/hr/jobs/${jobId}/leaderboard?${q}`, { cache: "no-store", signal });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { error?: { message?: string } };
        throw new Error(body.error?.message || "Không thể tải bảng xếp hạng.");
      }
      const body = await response.json() as LeaderboardPage;
      if (!signal?.aborted) { setItems(body); setError(""); }
    } catch (cause) { if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải bảng xếp hạng."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [jobId, page, minimum]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reload(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reload]);
  return <section className="resume-panel">
    <div className="resume-section-head"><div><h2>Kết quả matching hiện hành</h2><p>Thứ hạng do backend cung cấp; không tự tái xếp hạng trên trang.</p></div>
      <button type="button" className="button button-secondary" onClick={() => void reload()}>Làm mới</button></div>
    <div className="resume-filters"><label>Điểm tối thiểu (0–100)
      <input aria-label="Điểm tối thiểu" type="number" min={0} max={100} step=".1" value={minimum}
        onChange={(event) => { setPage(1); setMinimum(event.target.value); }} placeholder="Không lọc" /></label></div>
    {loading && <p role="status">Đang tải bảng xếp hạng…</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
    {!loading && !error && items?.data.length === 0 && <p className="resume-empty">Chưa có kết quả COMPLETED phù hợp. Có thể JD chưa được matching hoặc kết quả đang xử lý.</p>}
    {!!items?.data.length && <div className="resume-table-wrap"><table className="resume-table">
      <thead><tr><th scope="col">Hạng</th><th scope="col">Ứng viên trong talent pool</th><th scope="col">Điểm tổng</th><th scope="col">Kỹ năng</th><th scope="col">Ngữ nghĩa</th><th scope="col">Kinh nghiệm</th><th scope="col">Kết quả</th></tr></thead>
      <tbody>{items.data.map((item) => <tr key={item.match.id}>
        <td><strong>#{item.rank}</strong></td>
        <td><strong>{item.candidate.full_name || "Chưa có tên"}</strong><small>{item.candidate.current_title || "Chưa có chức danh"}</small></td>
        <td><strong>{safeScore(item.match.overall_score)?.toFixed(1) ?? "—"}</strong></td>
        <td>{safeScore(item.match.skill_score)?.toFixed(1) ?? "—"}</td>
        <td>{safeScore(item.match.semantic_score)?.toFixed(1) ?? "—"}</td>
        <td>{safeScore(item.match.experience_score)?.toFixed(1) ?? "—"}</td>
        <td><Link className="inline-link" href={`/matching/${item.match.id}`}>Phân tích →</Link></td>
      </tr>)}</tbody></table></div>}
    {items && items.meta.total_pages > 1 && <div className="resume-pagination">
      <button type="button" disabled={page <= 1} onClick={() => { setLoading(true); setPage(page - 1); }}>Trước</button>
      <span>Trang {page}/{items.meta.total_pages}</span>
      <button type="button" disabled={page >= items.meta.total_pages} onClick={() => { setLoading(true); setPage(page + 1); }}>Sau</button>
    </div>}
  </section>;
}
