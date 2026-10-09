import type { Metadata } from "next";
import { BRAND_NAME, siteConfig } from "@/lib/site-config";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: siteConfig.siteUrl ?? undefined,
  applicationName: BRAND_NAME,
  title: {
    default: `${BRAND_NAME} — Phân tích CV và mức độ phù hợp công việc`,
    template: `%s | ${BRAND_NAME}`,
  },
  description:
    "Nền tảng phân tích CV, đối chiếu mô tả công việc và nhận diện khoảng cách kỹ năng bằng AI/NLP.",
  category: "technology",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="vi">
      <body>
        <a className="skip-link" href="#noi-dung-chinh">
          Chuyển đến nội dung chính
        </a>
        {children}
      </body>
    </html>
  );
}
