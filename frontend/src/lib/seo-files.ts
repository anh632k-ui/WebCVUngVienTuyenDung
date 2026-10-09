import type { MetadataRoute } from "next";
import { absoluteUrl, PUBLIC_ROUTES, type SiteConfig } from "./site-config.ts";

export function createSitemap(config: SiteConfig): MetadataRoute.Sitemap {
  if (!config.indexingEnabled || !config.siteUrl) return [];

  return PUBLIC_ROUTES.map((route) => ({
    url: absoluteUrl(route, config.siteUrl)!,
    changeFrequency: route === "/" ? "weekly" : "monthly",
    priority: route === "/" ? 1 : 0.8,
  }));
}

export function createRobots(config: SiteConfig): MetadataRoute.Robots {
  if (!config.indexingEnabled || !config.siteUrl) {
    return { rules: { userAgent: "*", allow: "/" } };
  }

  return {
    rules: { userAgent: "*", allow: "/" },
    sitemap: absoluteUrl("/sitemap.xml", config.siteUrl),
  };
}
