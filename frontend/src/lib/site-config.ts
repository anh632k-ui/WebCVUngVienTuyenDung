export const BRAND_NAME = "CVInsight";

export const PUBLIC_ROUTES = ["/", "/tinh-nang", "/huong-dan"] as const;

type Environment = Readonly<Record<string, string | undefined>>;

function parseProductionOrigin(value: string | undefined): URL | null {
  if (!value) return null;

  try {
    const url = new URL(value.trim());
    const isLocalHost =
      url.hostname === "localhost" ||
      url.hostname === "127.0.0.1" ||
      url.hostname === "::1" ||
      url.hostname.endsWith(".local");

    if (
      url.protocol !== "https:" ||
      isLocalHost ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      (url.pathname !== "/" && url.pathname !== "")
    ) {
      return null;
    }

    return new URL(url.origin);
  } catch {
    return null;
  }
}

export function readSiteConfig(environment: Environment = process.env) {
  const siteUrl = parseProductionOrigin(environment.SITE_URL);
  const indexingEnabled =
    environment.SEO_INDEXING_ENABLED?.trim().toLowerCase() === "true" &&
    siteUrl !== null;

  return {
    siteUrl,
    indexingEnabled,
  } as const;
}

export const siteConfig = readSiteConfig();

export function absoluteUrl(path: string, baseUrl = siteConfig.siteUrl) {
  return baseUrl ? new URL(path, baseUrl).toString() : undefined;
}
