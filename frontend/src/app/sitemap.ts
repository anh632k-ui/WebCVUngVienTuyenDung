import type { MetadataRoute } from "next";
import { absoluteUrl, PUBLIC_ROUTES, siteConfig } from "@/lib/site-config";

export default function sitemap(): MetadataRoute.Sitemap {
  if (!siteConfig.indexingEnabled) return [];

  return PUBLIC_ROUTES.map((route) => ({
    url: absoluteUrl(route)!,
    changeFrequency: route === "/" ? "weekly" : "monthly",
    priority: route === "/" ? 1 : 0.8,
  }));
}
