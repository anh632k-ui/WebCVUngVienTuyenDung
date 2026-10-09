import type { Metadata } from "next";
import {
  absoluteUrl,
  BRAND_NAME,
  PUBLIC_PAGES,
  siteConfig,
  type SiteConfig,
} from "./site-config.ts";

type PageMetadata = {
  title: string;
  description: string;
  path: string;
};

export function createPageMetadata({
  title,
  description,
  path,
}: PageMetadata, config: SiteConfig = siteConfig): Metadata {
  const canonical = absoluteUrl(path, config.siteUrl);
  const socialImage = absoluteUrl("/og", config.siteUrl);

  return {
    title,
    description,
    alternates: canonical ? { canonical } : undefined,
    robots: config.indexingEnabled
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

export function createWebsiteJsonLd(config: SiteConfig = siteConfig) {
  if (!config.indexingEnabled || !config.siteUrl) return null;

  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: BRAND_NAME,
    url: absoluteUrl("/", config.siteUrl),
    inLanguage: "vi",
    description: PUBLIC_PAGES.home.description,
  } as const;
}
