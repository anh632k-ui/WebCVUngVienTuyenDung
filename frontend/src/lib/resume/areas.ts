/** Client-side route mapping is fixed per role; no arbitrary API origins. */
export type ResumeArea = "candidate" | "hr";
export const RESUME_AREAS = {
  candidate: { api: "/api/candidate/resumes", page: "/cv" },
  hr: { api: "/api/hr/resumes", page: "/hr/talent-pool" },
} as const satisfies Record<ResumeArea, { api: string; page: string }>;
