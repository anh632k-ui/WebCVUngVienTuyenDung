export type UserRole = "CANDIDATE" | "HR" | "ADMIN";
export type RegistrationRole = Exclude<UserRole, "ADMIN">;

export type CurrentUser = {
  id: string;
  email: string;
  full_name: string;
  phone_number: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type ApiErrorBody = {
  success: false;
  error: { code: string; message: string };
};

export type ApiSuccess<T> = { success: true; data: T };
