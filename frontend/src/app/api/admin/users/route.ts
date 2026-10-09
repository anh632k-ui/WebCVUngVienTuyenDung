import { NextRequest } from "next/server";
import { jsonNoStore } from "@/lib/auth/route-utils";
import { adminUserQuery } from "@/lib/admin/contracts";
import { adminBackend, adminGate, adminJson } from "@/lib/admin/gateway";
export async function GET(request: NextRequest) {
  const gate = await adminGate();
  if ("error" in gate) return gate.error;
  const q = adminUserQuery(request.nextUrl.searchParams);
  if (!q) return jsonNoStore({ success: false, error: { code: "VALIDATION_ERROR", message: "Bộ lọc tài khoản không hợp lệ." } }, 422);
  return adminJson(await adminBackend(`/admin/users?${q}`, gate.token));
}
