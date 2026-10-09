"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function logout() {
    if (pending) return;
    setPending(true); setError("");
    try {
      const response = await fetch("/api/session/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!response.ok) { setError("Không thể đăng xuất. Vui lòng thử lại."); return; }
      router.replace("/dang-nhap"); router.refresh();
    } catch { setError("Không thể kết nối máy chủ để đăng xuất."); }
    finally { setPending(false); }
  }
  return <div><button className="dashboard-logout" type="button" onClick={logout} disabled={pending}>{pending ? "Đang đăng xuất…" : "Đăng xuất"}</button>{error && <p className="form-error" role="alert">{error}</p>}</div>;
}
