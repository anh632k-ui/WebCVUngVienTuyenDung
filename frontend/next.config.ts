import type { NextConfig } from "next";

const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const PRIVATE_NO_STORE = [{ key: "Cache-Control", value: "private, no-store, max-age=0" }];

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      { source: "/dashboard/:path*", headers: PRIVATE_NO_STORE },
      { source: "/cv/:path*", headers: PRIVATE_NO_STORE },
      { source: "/hr/jobs/:path*", headers: PRIVATE_NO_STORE },
      { source: "/hr/talent-pool/:path*", headers: PRIVATE_NO_STORE },
      { source: "/matching/:path*", headers: PRIVATE_NO_STORE },
      { source: "/admin/users/:path*", headers: PRIVATE_NO_STORE },
      { source: "/tai-khoan", headers: PRIVATE_NO_STORE },
      { source: "/doi-mat-khau", headers: PRIVATE_NO_STORE },
      { source: "/dang-nhap", headers: PRIVATE_NO_STORE },
      { source: "/dang-ky", headers: PRIVATE_NO_STORE },
      { source: "/trang-thai-phien", headers: PRIVATE_NO_STORE },
    ];
  },
};

export default nextConfig;
