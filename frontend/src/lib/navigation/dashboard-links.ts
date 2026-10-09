import type { UserRole } from "@/lib/auth/types";

export type DashboardLink = { href: string; label: string; description: string };

const actions: Record<UserRole, readonly DashboardLink[]> = {
  CANDIDATE: [
    { href: "/cv", label: "Quản lý hồ sơ CV", description: "Tải CV, kiểm tra trạng thái xử lý và hiệu chỉnh dữ liệu nhận diện." },
    { href: "/matching", label: "Tự đối chiếu CV với JD", description: "Xem điểm thành phần và Skill Gap khi kết quả được tính xong." },
  ],
  HR: [
    { href: "/hr/jobs", label: "Quản lý mô tả công việc", description: "Tạo JD, xác nhận criteria, thay đổi trạng thái và trọng số." },
    { href: "/matching", label: "Batch matching", description: "Đối chiếu JD với CV trong talent pool thuộc quyền sở hữu của HR." },
  ],
  ADMIN: [
    { href: "/admin/users", label: "Quản lý tài khoản", description: "Lọc tài khoản, khóa/mở khóa và đổi vai trò theo quy tắc backend." },
  ],
};

export function getDashboardLinks(role: UserRole): readonly DashboardLink[] {
  return actions[role];
}

export function currentDashboardRoute(pathname: string, href: string): boolean {
  if (href.startsWith("/dashboard/")) return pathname === href;
  return pathname === href || pathname.startsWith(href + "/");
}
