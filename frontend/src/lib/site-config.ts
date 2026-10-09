export const BRAND_NAME = "CVInsight";

export const PUBLIC_PAGES = {
  home: {
    path: "/",
    title: "Phân tích CV và mức độ phù hợp công việc",
    description:
      "Phân tích CV, đối chiếu mô tả công việc và nhận diện khoảng cách kỹ năng bằng AI/NLP trong một quy trình minh bạch.",
  },
  features: {
    path: "/tinh-nang",
    title: "Tính năng phân tích CV và đối chiếu JD",
    description:
      "Khám phá phạm vi MVP: phân tích CV, tiêu chí JD, hybrid matching, Skill Gap, leaderboard và phân quyền Candidate, HR, Admin.",
  },
  guide: {
    path: "/huong-dan",
    title: "Hướng dẫn chuẩn bị CV và đọc kết quả matching",
    description:
      "Cách chuẩn bị CV PDF/DOCX, hiểu quy trình trích xuất, ba nhóm điểm, Skill Gap và nguyên tắc quyền riêng tư trên CVInsight.",
  },
} as const;

export const PUBLIC_ROUTES = Object.values(PUBLIC_PAGES).map(
  (page) => page.path,
);

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
  const configuredSiteUrl = parseProductionOrigin(environment.SITE_URL);
  const indexingEnabled =
    environment.SEO_INDEXING_ENABLED?.trim().toLowerCase() === "true" &&
    configuredSiteUrl !== null;

  return {
    // No public origin is exposed until both gates pass. Every metadata
    // consumer therefore shares the same indexability decision.
    siteUrl: indexingEnabled ? configuredSiteUrl : null,
    indexingEnabled,
  } as const;
}

export type SiteConfig = ReturnType<typeof readSiteConfig>;

export const siteConfig = readSiteConfig();

export function absoluteUrl(path: string, baseUrl = siteConfig.siteUrl) {
  return baseUrl ? new URL(path, baseUrl).toString() : undefined;
}
