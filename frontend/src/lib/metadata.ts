import type { Metadata } from "next";
import { absoluteUrl, BRAND_NAME, siteConfig } from "./site-config";

type PageMetadata = {
  title: string;
  description: string;
  path: string;
};

export function createPageMetadata({
  title,
  description,
  path,
}: PageMetadata): Metadata {
  const canonical = absoluteUrl(path);
  const socialImage = absoluteUrl("/og");

  return {
    title,
    description,
    alternates: canonical ? { canonical } : undefined,
    robots: siteConfig.indexingEnabled
      ? { index: true, follow: true }
      : { index: false, follow: true, googleBot: { index: false, follow: true } },
    openGraph: {
      type: "website",
      locale: "vi_VN",
      siteName: BRAND_NAME,
      title,
      description,
      url: canonical,
      images: socialImage
        ? [{ url: socialImage, width: 1200, height: 630, alt: `${BRAND_NAME} — Phân tích CV và mức độ phù hợp công việc` }]
        : undefined,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: socialImage ? [socialImage] : undefined,
    },
  };
}
