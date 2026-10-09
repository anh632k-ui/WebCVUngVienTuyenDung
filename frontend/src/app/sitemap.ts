import type { MetadataRoute } from "next";
import { createSitemap } from "@/lib/seo-files";
import { siteConfig } from "@/lib/site-config";

export default function sitemap(): MetadataRoute.Sitemap {
  return createSitemap(siteConfig);
}
