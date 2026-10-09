"use client";

import { useCallback, useEffect, useState } from "react";
import type { AdminPage, AdminUser } from "@/lib/admin/contracts";
import type { UserRole } from "@/lib/auth/types";

const roleLabels = { CANDIDATE: "Ứng viên", HR: "Nhà tuyển dụng", ADMIN: "Quản trị" } as const;
async function errorFor(response: Response) {
  const body = await response.json().catch(() => ({})) as { error?: { code?: string; message?: string } };
  return response.status === 409 ? "Không thể đổi vai trò: tài khoản còn sở hữu CV/JD chưa xóa. Không tự xóa dữ liệu để chuyển role." :
    body.error?.message || "Không thể thực hiện thao tác quản trị.";
}

function AdminUserRow({ user, currentUserId, update }: {
  user: AdminUser; currentUserId: string; update: (id: string, payload: { role?: "CANDIDATE" | "HR"; is_active?: boolean }) => Promise<void>;
}) {
  const [selectedRole, setSelectedRole] = useState<"CANDIDATE" | "HR">(user.role === "ADMIN" ? "CANDIDATE" : user.role);
  const canEditRole = user.role !== "ADMIN" && user.id !== currentUserId;
  const canToggle = user.id !== currentUserId;
  return <tr>
    <td><strong>{user.full_name}</strong><small>{user.email}</small></td>
    <td><span className="resume-status">{roleLabels[user.role]}</span></td>
    <td><span className={`resume-status ${user.is_active ? "resume-status-parsed" : "resume-status-failed"}`}>{user.is_active ? "Hoạt động" : "Vô hiệu hóa"}</span></td>
    <td>{new Date(user.created_at).toLocaleDateString("vi-VN")}</td>
    <td><div className="admin-row-actions">
      {canEditRole ? <>
        <label className="sr-only" htmlFor={`role-${user.id}`}>Vai trò mới cho {user.email}</label>
        <select id={`role-${user.id}`} value={selectedRole} onChange={(event) => setSelectedRole(event.target.value as "CANDIDATE" | "HR")}>
          <option value="CANDIDATE">Ứng viên</option><option value="HR">Nhà tuyển dụng</option>
        </select>
        <button type="button" disabled={selectedRole === user.role} onClick={() => {
          if (window.confirm(`Đổi vai trò ${user.email} từ ${roleLabels[user.role]} sang ${roleLabels[selectedRole]}? Backend sẽ từ chối nếu còn tài nguyên nghiệp vụ.`))
            void update(user.id, { role: selectedRole });
        }}>Lưu role</button>
      </> : <span className="resume-hint">{user.id === currentUserId ? "Tài khoản hiện tại" : "Role Admin không đổi tại đây"}</span>}
      <button type="button" disabled={!canToggle} onClick={() => {
        if (window.confirm(`${user.is_active ? "Vô hiệu hóa" : "Kích hoạt"} tài khoản ${user.email}?`))
          void update(user.id, { is_active: !user.is_active });
      }}>{user.is_active ? "Khóa" : "Mở khóa"}</button>
    </div></td>
  </tr>;
}

export function AdminUsersWorkspace({ currentUserId }: { currentUserId: string }) {
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [role, setRole] = useState<UserRole | "">("");
  const [active, setActive] = useState<"" | "true" | "false">("");
  const [data, setData] = useState<AdminPage | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);

  const reload = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ page: String(page), limit: "20" });
    if (keyword.trim()) query.set("keyword", keyword.trim());
    if (role) query.set("role", role);
    if (active) query.set("is_active", active);
    try {
      const response = await fetch(`/api/admin/users?${query}`, { cache: "no-store", signal });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { error?: { message?: string } };
        throw new Error(body.error?.message || "Không thể tải tài khoản.");
      }
      const body = await response.json() as AdminPage;
      if (!signal?.aborted) { setData(body); setError(""); }
    } catch (cause) { if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Không thể tải tài khoản."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [page, keyword, role, active]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!controller.signal.aborted) void reload(controller.signal); }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [reload]);

  async function update(id: string, payload: { role?: "CANDIDATE" | "HR"; is_active?: boolean }) {
    if (working) return;
    setWorking(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/admin/users/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error(await errorFor(response));
      setNotice("Đã cập nhật tài khoản thành công."); await reload();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Không thể cập nhật tài khoản."); }
    finally { setWorking(false); }
  }

  return <section className="resume-panel">
    <div className="resume-section-head"><div><h2>Danh sách tài khoản</h2><p>Thay đổi được backend kiểm tra và không ảnh hưởng tự động tới dữ liệu CV/JD thuộc quyền sở hữu.</p></div>
      <button type="button" className="button button-secondary" disabled={working} onClick={() => void reload()}>Làm mới</button></div>
    <div className="resume-filters">
      <label>Tìm kiếm<input maxLength={120} value={keyword} placeholder="Họ tên hoặc email…" onChange={(event) => { setPage(1); setKeyword(event.target.value); }} /></label>
      <label>Vai trò<select value={role} onChange={(event) => { setPage(1); setRole(event.target.value as UserRole | ""); }}>
        <option value="">Tất cả</option><option value="CANDIDATE">Ứng viên</option><option value="HR">Nhà tuyển dụng</option><option value="ADMIN">Quản trị</option>
      </select></label>
      <label>Trạng thái<select value={active} onChange={(event) => { setPage(1); setActive(event.target.value as "" | "true" | "false"); }}>
        <option value="">Tất cả</option><option value="true">Hoạt động</option><option value="false">Vô hiệu hóa</option>
      </select></label>
    </div>
    {loading && <p role="status">Đang tải danh sách người dùng…</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
    {notice && <p role="status" className="form-success">{notice}</p>}
    {!loading && !error && data?.data.length === 0 && <p className="resume-empty">Không tìm thấy tài khoản phù hợp.</p>}
    {!!data?.data.length && <div className="resume-table-wrap"><table className="resume-table">
      <thead><tr><th scope="col">Tài khoản</th><th scope="col">Vai trò</th><th scope="col">Trạng thái</th><th scope="col">Ngày tạo</th><th scope="col">Thao tác</th></tr></thead>
      <tbody>{data.data.map((user) => <AdminUserRow key={user.id + ":" + user.role} user={user} currentUserId={currentUserId} update={update} />)}</tbody>
    </table></div>}
    {data && data.meta.total_pages > 1 && <div className="resume-pagination">
      <button type="button" disabled={page <= 1} onClick={() => { setLoading(true); setPage(page - 1); }}>Trước</button>
      <span>Trang {page}/{data.meta.total_pages}</span>
      <button type="button" disabled={page >= data.meta.total_pages} onClick={() => { setLoading(true); setPage(page + 1); }}>Sau</button>
    </div>}
  </section>;
}
