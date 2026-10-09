import assert from "node:assert/strict";
import test from "node:test";
import { createPageMetadata, createWebsiteJsonLd } from "../src/lib/metadata.ts";
import { createRobots, createSitemap } from "../src/lib/seo-files.ts";
import { PUBLIC_PAGES, PUBLIC_ROUTES, readSiteConfig } from "../src/lib/site-config.ts";

const productionOrigin = "https://seo-fixture.test";
const disabledConfig = readSiteConfig({ SITE_URL: productionOrigin, SEO_INDEXING_ENABLED: "false" });
const enabledConfig = readSiteConfig({ SITE_URL: productionOrigin, SEO_INDEXING_ENABLED: "true" });

test("SEO mặc định tắt và không công khai origin khi thiếu cấu hình", () => {
  const config = readSiteConfig({});
  assert.equal(config.siteUrl, null);
  assert.equal(config.indexingEnabled, false);
});

test("SITE_URL hợp lệ vẫn không công khai origin khi cờ SEO tắt", () => {
  assert.equal(disabledConfig.siteUrl, null);
  assert.equal(disabledConfig.indexingEnabled, false);
});

test("không bật index với HTTP, localhost, path hoặc URL không hợp lệ", () => {
  for (const siteUrl of ["http://seo-fixture.test", "https://localhost:3000", "https://127.0.0.1", "https://seo-fixture.test/path", "not-a-url"]) {
    const config = readSiteConfig({ SITE_URL: siteUrl, SEO_INDEXING_ENABLED: "true" });
    assert.equal(config.siteUrl, null);
    assert.equal(config.indexingEnabled, false);
  }
});

test("bật index với HTTPS origin hợp lệ và cờ tường minh", () => {
  assert.equal(enabledConfig.siteUrl?.toString(), `${productionOrigin}/`);
  assert.equal(enabledConfig.indexingEnabled, true);
});

test("metadata đủ ba trang ở chế độ OFF chỉ phát noindex, follow", () => {
  for (const page of Object.values(PUBLIC_PAGES)) {
    const metadata = createPageMetadata(page, disabledConfig);
    assert.equal(metadata.title, page.title);
    assert.equal(metadata.description, page.description);
    assert.deepEqual(metadata.robots, { index: false, follow: true, googleBot: { index: false, follow: true } });
    assert.equal(metadata.alternates, undefined);
    assert.equal(metadata.openGraph && "url" in metadata.openGraph ? metadata.openGraph.url : undefined, undefined);
    assert.equal(metadata.openGraph && "images" in metadata.openGraph ? metadata.openGraph.images : undefined, undefined);
    assert.equal(metadata.twitter && "images" in metadata.twitter ? metadata.twitter.images : undefined, undefined);
  }
  assert.equal(createWebsiteJsonLd(disabledConfig), null);
});

test("metadata đủ ba trang ở chế độ ON có canonical, OG và Twitter đúng", () => {
  for (const page of Object.values(PUBLIC_PAGES)) {
    const metadata = createPageMetadata(page, enabledConfig);
    const expectedUrl = new URL(page.path, productionOrigin).toString();
    assert.deepEqual(metadata.robots, { index: true, follow: true });
    assert.equal(metadata.alternates?.canonical, expectedUrl);
    assert.equal(metadata.openGraph && "url" in metadata.openGraph ? metadata.openGraph.url : undefined, expectedUrl);
    assert.ok(metadata.openGraph && "images" in metadata.openGraph && metadata.openGraph.images);
    assert.ok(metadata.twitter && "images" in metadata.twitter && metadata.twitter.images);
  }
  assert.equal(createWebsiteJsonLd(enabledConfig)?.url, `${productionOrigin}/`);
});

test("sitemap và robots cùng tuân theo SEO gate", () => {
  assert.deepEqual(createSitemap(disabledConfig), []);
  assert.deepEqual(createRobots(disabledConfig), { rules: { userAgent: "*", allow: "/" } });

  const sitemap = createSitemap(enabledConfig);
  assert.deepEqual(sitemap.map((entry) => entry.url), PUBLIC_ROUTES.map((route) => new URL(route, productionOrigin).toString()));
  assert.equal(createRobots(enabledConfig).sitemap, `${productionOrigin}/sitemap.xml`);
  assert.equal(sitemap.length, 3);
  assert.equal(sitemap.some((entry) => /dashboard|admin|resumes|matching|api/i.test(entry.url)), false);
});
