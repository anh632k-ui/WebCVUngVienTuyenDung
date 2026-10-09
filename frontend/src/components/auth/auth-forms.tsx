"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { dashboardForRole } from "@/lib/auth/roles";
import type { CurrentUser } from "@/lib/auth/types";

type ErrorResponse = { error?: { code?: string; message?: string } };

async function responseBody(response: Response): Promise<ErrorResponse & { data?: unknown }> {
  try { return await response.json(); } catch { return {}; }
}

function messageFor(response: Response, body: ErrorResponse, fallback: string) {
  if (body.error?.code === "INVALID_CREDENTIALS") return "Email hoặc mật khẩu không đúng.";
  if (body.error?.code === "ACCOUNT_INACTIVE") return "Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ quản trị viên.";
  if (body.error?.code === "EMAIL_ALREADY_EXISTS") return "Email này đã được sử dụng.";
  if (body.error?.code === "CURRENT_PASSWORD_INCORRECT") return "Mật khẩu hiện tại không đúng.";
  if (body.error?.code === "VALIDATION_ERROR") return "Thông tin chưa hợp lệ. Vui lòng kiểm tra lại.";
  if (response.status === 503) return "Không thể kết nối máy chủ. Vui lòng thử lại sau.";
  return body.error?.message || fallback;
}

function PasswordInput({ id, name, label, autoComplete, minLength, value, onChange, describedBy, invalid }: {
  id: string; name: string; label: string; autoComplete: string; minLength?: number;
  value: string; onChange: (value: string) => void; describedBy?: string; invalid?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      <div className="password-control">
        <input id={id} name={name} type={visible ? "text" : "password"} autoComplete={autoComplete} minLength={minLength} required value={value} aria-invalid={invalid || undefined} aria-describedby={describedBy} onChange={(event) => onChange(event.target.value)} />
        <button type="button" aria-label={`${visible ? "Ẩn" : "Hiện"} ${label.toLowerCase()}`} aria-pressed={visible} onClick={() => setVisible((current) => !current)}>{visible ? "Ẩn" : "Hiện"}</button>
      </div>
    </div>
  );
}

export function LoginForm({ sessionExpired = false, successMessage }: { sessionExpired?: boolean; successMessage?: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/session/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: form.get("email"), password }) });
      const body = await responseBody(response);
      if (!response.ok) { setError(messageFor(response, body, "Đăng nhập không thành công.")); return; }
      const user = (body.data as { user?: CurrentUser } | undefined)?.user;
      if (!user) { setError("Không thể xác nhận tài khoản sau đăng nhập."); return; }
      setPassword(""); router.replace(dashboardForRole(user.role)); router.refresh();
    } catch { setError("Không thể kết nối máy chủ. Vui lòng thử lại sau."); }
    finally { setPending(false); }
  }

  return (
    <div className="auth-form-card">
      <div className="form-heading"><h2>Đăng nhập</h2><p>Tiếp tục vào không gian làm việc của bạn.</p></div>
      {sessionExpired && <div className="form-notice" role="status">Phiên đăng nhập không còn hợp lệ. Vui lòng đăng nhập lại.</div>}
      {successMessage && <div className="form-success notice" role="status">{successMessage}</div>}
      <form onSubmit={submit} aria-describedby={error ? "login-error" : undefined}>
        <div className="form-field"><label htmlFor="login-email">Email</label><input id="login-email" name="email" type="email" autoComplete="email" inputMode="email" required /></div>
        <PasswordInput id="login-password" name="password" label="Mật khẩu" autoComplete="current-password" value={password} onChange={setPassword} />
        {error && <p id="login-error" className="form-error" role="alert">{error}</p>}
        <button className="button button-primary form-submit" type="submit" disabled={pending}>{pending ? "Đang đăng nhập…" : "Đăng nhập"}</button>
      </form>
      <p className="form-helper">Chưa hỗ trợ đặt lại mật khẩu trong phiên bản MVP.</p>
    </div>
  );
}

export function RegisterForm() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [confirmationError, setConfirmationError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setError(""); setConfirmationError("");
    if (password !== confirmation) { setConfirmationError("Mật khẩu nhập lại chưa khớp."); return; }
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/session/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ full_name: form.get("full_name"), email: form.get("email"), phone_number: form.get("phone_number") || null, password, role: form.get("role") }) });
      const body = await responseBody(response);
      if (!response.ok) { setError(messageFor(response, body, "Đăng ký không thành công.")); return; }
      setPassword(""); setConfirmation(""); router.push("/dang-nhap?registered=1");
    } catch { setError("Không thể kết nối máy chủ. Vui lòng thử lại sau."); }
    finally { setPending(false); }
  }

  return (
    <div className="auth-form-card">
      <div className="form-heading"><h2>Tạo tài khoản</h2><p>Chọn vai trò phù hợp với cách bạn sử dụng nền tảng.</p></div>
      <form onSubmit={submit} aria-describedby={error ? "register-error" : undefined}>
        <div className="form-field"><label htmlFor="register-name">Họ và tên</label><input id="register-name" name="full_name" autoComplete="name" minLength={1} maxLength={100} required /></div>
        <div className="form-row">
          <div className="form-field"><label htmlFor="register-email">Email</label><input id="register-email" name="email" type="email" autoComplete="email" required /></div>
          <div className="form-field"><label htmlFor="register-phone">Số điện thoại <span>(tùy chọn)</span></label><input id="register-phone" name="phone_number" type="tel" autoComplete="tel" maxLength={20} /></div>
        </div>
        <PasswordInput id="register-password" name="password" label="Mật khẩu" autoComplete="new-password" minLength={8} value={password} onChange={setPassword} />
        <PasswordInput id="register-confirm" name="password_confirmation" label="Nhập lại mật khẩu" autoComplete="new-password" minLength={8} value={confirmation} onChange={(value) => { setConfirmation(value); setConfirmationError(""); }} describedBy={confirmationError ? "confirmation-error" : undefined} invalid={Boolean(confirmationError)} />
        {confirmationError && <p id="confirmation-error" className="form-error" role="alert">{confirmationError}</p>}
        <fieldset className="role-choice">
          <legend>Loại tài khoản</legend>
          <label><input type="radio" name="role" value="CANDIDATE" defaultChecked /><span><strong>Ứng viên</strong><small>Quản lý CV và tự đối chiếu công việc</small></span></label>
          <label><input type="radio" name="role" value="HR" /><span><strong>Nhà tuyển dụng</strong><small>Quản lý JD và talent pool thuộc sở hữu</small></span></label>
        </fieldset>
        {error && <p id="register-error" className="form-error" role="alert">{error}</p>}
        <button className="button button-primary form-submit" type="submit" disabled={pending}>{pending ? "Đang tạo tài khoản…" : "Tạo tài khoản"}</button>
      </form>
    </div>
  );
}
