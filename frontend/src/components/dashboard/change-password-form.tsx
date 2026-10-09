"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

export function ChangePasswordForm() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (pending) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const nextPassword = String(form.get("new_password") ?? "");
    if (nextPassword !== String(form.get("confirmation") ?? "")) { setError("Mật khẩu nhập lại chưa khớp."); return; }
    setPending(true); setError("");
    try {
      const response = await fetch("/api/session/change-password", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: form.get("current_password"), new_password: nextPassword }) });
      const body = await response.json().catch(() => ({})) as { error?: { code?: string; message?: string } };
      if (!response.ok) { setError(body.error?.code === "CURRENT_PASSWORD_INCORRECT" ? "Mật khẩu hiện tại không đúng." : body.error?.message || "Không thể đổi mật khẩu."); return; }
      formElement.reset(); router.replace("/dang-nhap?password_changed=1"); router.refresh();
    } catch { setError("Không thể kết nối máy chủ."); }
    finally { setPending(false); }
  }
  return <form className="settings-form" onSubmit={submit}><div className="form-field"><label htmlFor="current-password">Mật khẩu hiện tại</label><input id="current-password" name="current_password" type="password" autoComplete="current-password" required /></div><div className="form-field"><label htmlFor="new-password">Mật khẩu mới</label><input id="new-password" name="new_password" type="password" autoComplete="new-password" minLength={8} required /></div><div className="form-field"><label htmlFor="confirm-password">Nhập lại mật khẩu mới</label><input id="confirm-password" name="confirmation" type="password" autoComplete="new-password" minLength={8} required /></div>{error && <p className="form-error" role="alert">{error}</p>}<button className="button button-primary" type="submit" disabled={pending}>{pending ? "Đang cập nhật…" : "Đổi mật khẩu"}</button><p className="form-helper">Sau khi đổi thành công, cookie phiên sẽ bị xóa và bạn cần đăng nhập lại. Backend MVP chưa thu hồi JWT đã phát hành trước khi token hết hạn.</p></form>;
}
