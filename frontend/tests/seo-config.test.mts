import assert from "node:assert/strict";
import test from "node:test";
import { PUBLIC_ROUTES, readSiteConfig } from "../src/lib/site-config.ts";

test("SEO mặc định tắt khi thiếu cấu hình", () => {
  const config = readSiteConfig({});
  assert.equal(config.siteUrl, null);
  assert.equal(config.indexingEnabled, false);
});

test("không bật index nếu URL không phải HTTPS production origin", () => {
  for (const siteUrl of [
    "http://cvinsight.vn",
    "https://localhost:3000",
    "https://127.0.0.1",
    "https://cvinsight.vn/path",
    "not-a-url",
  ]) {
    assert.equal(
      readSiteConfig({ SITE_URL: siteUrl, SEO_INDEXING_ENABLED: "true" })
        .indexingEnabled,
      false,
    );
  }
});

test("chỉ bật index khi có HTTPS origin và cờ tường minh", () => {
  const disabled = readSiteConfig({ SITE_URL: "https://cvinsight.vn" });
  assert.equal(disabled.indexingEnabled, false);

  const enabled = readSiteConfig({
    SITE_URL: "https://cvinsight.vn/",
    SEO_INDEXING_ENABLED: "TRUE",
  });
  assert.equal(enabled.siteUrl?.toString(), "https://cvinsight.vn/");
  assert.equal(enabled.indexingEnabled, true);
});

test("sitemap chỉ có đúng ba route public", () => {
  assert.deepEqual(PUBLIC_ROUTES, ["/", "/tinh-nang", "/huong-dan"]);
});
