import { jsonNoStore, rejectUnsafeMutation } from "@/lib/auth/route-utils";
import { expiredSessionCookieOptions, SESSION_COOKIE } from "@/lib/auth/security";

export async function POST(request: Request) {
  const rejected = rejectUnsafeMutation(request);
  if (rejected) return rejected;
  const response = jsonNoStore({ success: true, data: { message: "Đã xóa phiên đăng nhập trên trình duyệt." } });
  response.cookies.set(SESSION_COOKIE, "", expiredSessionCookieOptions());
  return response;
}
