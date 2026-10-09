"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { CurrentUser } from "@/lib/auth/types";

export function ProfileForm({ user }: { user: CurrentUser }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (pending) return;
    setPending(true); setError(""); setMessage("");
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/session/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ full_name: form.get("full_name"), phone_number: form.get("phone_number") || null }) });
      const body = await response.json().catch(() => ({})) as { error?: { message?: string } };
      if (!response.ok) { setError(body.error?.message || "Không thể cập nhật hồ sơ."); return; }
      setMessage("Đã cập nhật hồ sơ."); router.refresh();
    } catch { setError("Không thể kết nối máy chủ."); }
    finally { setPending(false); }
  }
  return <form className="settings-form" onSubmit={submit}><div className="form-field"><label htmlFor="profile-name">Họ và tên</label><input id="profile-name" name="full_name" defaultValue={user.full_name} minLength={1} maxLength={100} autoComplete="name" required /></div><div className="form-field"><label htmlFor="profile-phone">Số điện thoại</label><input id="profile-phone" name="phone_number" defaultValue={user.phone_number ?? ""} maxLength={20} autoComplete="tel" /></div>{error && <p className="form-error" role="alert">{error}</p>}{message && <p className="form-success" role="status">{message}</p>}<button className="button button-primary" type="submit" disabled={pending}>{pending ? "Đang lưu…" : "Lưu thay đổi"}</button></form>;
}
