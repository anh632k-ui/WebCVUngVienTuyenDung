import type { CurrentUser, RegistrationRole, UserRole } from "./types.ts";

type ValidationResult<T> = { ok: true; value: T } | { ok: false; message: string };

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ROLES = new Set<UserRole>(["CANDIDATE", "HR", "ADMIN"]);

function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key));
}

function validEmail(value: unknown): value is string {
  return typeof value === "string" && value.length <= 254 && EMAIL_PATTERN.test(value);
}

export function validateLogin(value: unknown): ValidationResult<{ email: string; password: string }> {
  const body = objectValue(value);
  if (!body || !hasOnlyKeys(body, ["email", "password"])) return { ok: false, message: "Dữ liệu đăng nhập không hợp lệ." };
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!validEmail(email)) return { ok: false, message: "Email không hợp lệ." };
  if (typeof body.password !== "string" || body.password.length === 0) return { ok: false, message: "Vui lòng nhập mật khẩu." };
  return { ok: true, value: { email, password: body.password } };
}

export function validateRegistration(value: unknown): ValidationResult<{ email: string; password: string; full_name: string; phone_number: string | null; role: RegistrationRole }> {
  const body = objectValue(value);
  if (!body || !hasOnlyKeys(body, ["email", "password", "full_name", "phone_number", "role"])) return { ok: false, message: "Dữ liệu đăng ký không hợp lệ." };
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const fullName = typeof body.full_name === "string" ? body.full_name.trim() : "";
  const phone = body.phone_number === null || body.phone_number === undefined || body.phone_number === "" ? null : body.phone_number;
  if (!validEmail(email)) return { ok: false, message: "Email không hợp lệ." };
  if (fullName.length < 1 || fullName.length > 100) return { ok: false, message: "Họ tên phải có từ 1 đến 100 ký tự." };
  if (typeof body.password !== "string" || body.password.length < 8) return { ok: false, message: "Mật khẩu phải có ít nhất 8 ký tự." };
  if (typeof phone !== "string" && phone !== null) return { ok: false, message: "Số điện thoại không hợp lệ." };
  if (typeof phone === "string" && phone.trim().length > 20) return { ok: false, message: "Số điện thoại tối đa 20 ký tự." };
  if (body.role !== "CANDIDATE" && body.role !== "HR") return { ok: false, message: "Loại tài khoản không hợp lệ." };
  return { ok: true, value: { email, password: body.password, full_name: fullName, phone_number: typeof phone === "string" ? phone.trim() || null : null, role: body.role } };
}

export function validateProfile(value: unknown): ValidationResult<{ full_name: string; phone_number: string | null }> {
  const body = objectValue(value);
  if (!body || !hasOnlyKeys(body, ["full_name", "phone_number"])) return { ok: false, message: "Chỉ được cập nhật họ tên và số điện thoại." };
  const fullName = typeof body.full_name === "string" ? body.full_name.trim() : "";
  const phone = body.phone_number === null || body.phone_number === undefined || body.phone_number === "" ? null : body.phone_number;
  if (fullName.length < 1 || fullName.length > 100) return { ok: false, message: "Họ tên phải có từ 1 đến 100 ký tự." };
  if (typeof phone !== "string" && phone !== null) return { ok: false, message: "Số điện thoại không hợp lệ." };
  if (typeof phone === "string" && phone.trim().length > 20) return { ok: false, message: "Số điện thoại tối đa 20 ký tự." };
  return { ok: true, value: { full_name: fullName, phone_number: typeof phone === "string" ? phone.trim() || null : null } };
}

export function validatePasswordChange(value: unknown): ValidationResult<{ current_password: string; new_password: string }> {
  const body = objectValue(value);
  if (!body || !hasOnlyKeys(body, ["current_password", "new_password"])) return { ok: false, message: "Dữ liệu đổi mật khẩu không hợp lệ." };
  if (typeof body.current_password !== "string" || body.current_password.length === 0) return { ok: false, message: "Vui lòng nhập mật khẩu hiện tại." };
  if (typeof body.new_password !== "string" || body.new_password.length < 8) return { ok: false, message: "Mật khẩu mới phải có ít nhất 8 ký tự." };
  return { ok: true, value: { current_password: body.current_password, new_password: body.new_password } };
}

export function parseUser(value: unknown): CurrentUser | null {
  const body = objectValue(value);
  if (!body || typeof body.id !== "string" || !validEmail(body.email) || typeof body.full_name !== "string" || (body.phone_number !== null && typeof body.phone_number !== "string") || typeof body.role !== "string" || !ROLES.has(body.role as UserRole) || typeof body.is_active !== "boolean" || typeof body.created_at !== "string" || typeof body.updated_at !== "string") return null;
  return body as unknown as CurrentUser;
}
