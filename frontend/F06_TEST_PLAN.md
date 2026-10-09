# F06 – Automated Quality Gates & Remaining Live E2E

## Automated, verified by GitHub Actions on PR/dev

- Node 22, `npm ci`, ESLint, `npm test`, Next.js production build, `tsc --noEmit`.
- `git diff --check HEAD^ HEAD` now checks changed code; checkout fetches 2 commits.
- npm test includes 14+ unit cases and **production Next.js HTTP smoke using mocked FastAPI** for SEO OFF/ON, session/auth, Candidate CV, HR JD, Matching, Leaderboard and Admin.
- Security headers on responses: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, restrictive camera/microphone/geolocation Permissions-Policy, explicit Referrer-Policy.
- Protected/auth HTML routes additionally set `Cache-Control: private, no-store, max-age=0`. BFF auth/role routes already set no-store at handler level.
- No CSP added blindly: Next.js runtime scripts and F01 generated OG/image routes need a deliberate nonce/hashes policy and browser/production verification.

## Still needs local/live Codex verification; CI mocking is not proof

1. Bring up FastAPI, PostgreSQL+pgvector, Celery, broker, object storage, actual model embeddings and real test users.
2. Test Candidate registration/login expiry/cookie, role changes, inactive account, profile/password with actual DB.
3. Test real PDF/DOCX upload <=5MiB and invalid magic bytes, idempotency replay/conflict, parse state polling, recovery and revision concurrency; check owned CV download, parsed-data update, delete.
4. Test HR create JD/idempotency, real criteria taxonomy, ACTIVE preconditions, weights numeric precision, revision/generation invalidation and recalculate dispatch.
5. Test Candidate private self-matching with ACTIVE JD, HR batch matching in owned talent pool, actual embeddings/BGE-M3 + worker completion/failure and true score/gap/leaderboard. Assert cross-user privacy and stale/deleted exclusion.
6. Run real browser E2E on all main workflows at viewports 320/375/768/1024/1440px; inspect keyboard navigation, screen readers, cross-device form/layout and race/loading/error states.
7. Validate staging deployment HTTPS, reverse proxy Origin/Host, Secure HttpOnly cookies, CSRF, security headers and CORS. Do not set `SEO_INDEXING_ENABLED=true` until a real production origin/domain is approved.
8. Inspect `npm audit` advisory list. Do not apply `npm audit fix --force` without compatibility tests; assess risk from production vs development dependencies.

No backend/schema/canonical changes are made by this F06 branch. This branch is for CI and security response headers, **not** claim of production E2E completion.
