import type { MetadataRoute } from "next";
import { createRobots } from "@/lib/seo-files";
import { siteConfig } from "@/lib/site-config";

export default function robots(): MetadataRoute.Robots {
  return createRobots(siteConfig);
}
