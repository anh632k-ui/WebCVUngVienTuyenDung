import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";

const projectRoot = process.cwd();
const nextBin = path.join(projectRoot, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath;
const originFixture = "https://seo-fixture.test";
const pages = [
  ["/", "Phân tích CV và mức độ phù hợp công việc"],
  ["/tinh-nang", "Tính năng phân tích CV và đối chiếu JD"],
  ["/huong-dan", "Hướng dẫn chuẩn bị CV và đọc kết quả matching"],
];

function build(environment) {
  assert.ok(npmCli, "npm_execpath phải tồn tại khi chạy qua npm test");
  const result = spawnSync(process.execPath, [npmCli, "run", "build"], {
    cwd: projectRoot,
    env: { ...process.env, ...environment },
    stdio: "inherit",
  });
  assert.equal(result.status, 0, "next build phải thành công");
}

async function waitUntilReady(baseUrl, processLogs) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {
      // Server chưa sẵn sàng.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Production server không sẵn sàng.\n${processLogs()}`);
}

async function withProductionServer(port, environment, verify) {
  let logs = "";
  const server = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
    cwd: projectRoot,
    env: { ...process.env, ...environment },
    stdio: ["ignore", "pipe", "pipe"],
  });
  server.stdout.on("data", (chunk) => { logs += chunk.toString(); });
  server.stderr.on("data", (chunk) => { logs += chunk.toString(); });

  try {
    const baseUrl = `http://127.0.0.1:${port}`;
    await waitUntilReady(baseUrl, () => logs);
    await verify(baseUrl);
  } finally {
    server.kill();
    await Promise.race([
      new Promise((resolve) => server.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 3000)),
    ]);
  }
}

function verifyHeaders(response) {
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("x-frame-options"), "DENY");
  assert.equal(response.headers.get("referrer-policy"), "strict-origin-when-cross-origin");
  assert.match(response.headers.get("permissions-policy") ?? "", /camera=\(\)/);
}

async function readText(baseUrl, route, expectedStatus = 200) {
  const response = await fetch(`${baseUrl}${route}`);
  assert.equal(response.status, expectedStatus, `${route} phải trả ${expectedStatus}`);
  if (["/", "/tinh-nang", "/huong-dan"].includes(route)) verifyHeaders(response);
  return { response, text: await response.text() };
}

async function verifyOgImage(baseUrl) {
  const response = await fetch(`${baseUrl}/og`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^image\/png/);
  const image = Buffer.from(await response.arrayBuffer());
  assert.equal(image.subarray(1, 4).toString("ascii"), "PNG");
  assert.equal(image.readUInt32BE(16), 1200);
  assert.equal(image.readUInt32BE(20), 630);
  assert.ok(image.length > 10_000, "Ảnh OG không được rỗng");
}

async function verifyBrandIcon(baseUrl) {
  const response = await fetch(`${baseUrl}/icon.svg`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^image\/svg\+xml/);
  assert.match(await response.text(), /#2563eb/i);
}

async function verifyOff(baseUrl) {
  for (const [route, title] of pages) {
    const { text } = await readText(baseUrl, route);
    assert.ok(text.includes(title));
    assert.match(text, /<meta name="robots" content="noindex, follow"\/>/);
    assert.doesNotMatch(text, /rel="canonical"/);
    assert.doesNotMatch(text, /property="og:url"/);
    assert.doesNotMatch(text, /property="og:image"/);
    assert.doesNotMatch(text, /name="twitter:image"/);
    assert.doesNotMatch(text, /application\/ld\+json/);
  }

  const robots = await readText(baseUrl, "/robots.txt");
  assert.match(robots.text, /Allow: \//);
  assert.doesNotMatch(robots.text, /Sitemap:/i);

  const sitemap = await readText(baseUrl, "/sitemap.xml");
  assert.equal((sitemap.text.match(/<url>/g) ?? []).length, 0);
  await verifyOgImage(baseUrl);
  await verifyBrandIcon(baseUrl);
  await readText(baseUrl, "/khong-ton-tai", 404);
}

async function verifyOn(baseUrl) {
  for (const [route, title] of pages) {
    const { text } = await readText(baseUrl, route);
    const absolutePageUrl = new URL(route, originFixture).toString().replace(/\/$/, route === "/" ? "" : "");
    assert.ok(text.includes(title));
    assert.match(text, /<meta name="robots" content="index, follow"\/>/);
    assert.ok(text.includes(`rel="canonical" href="${absolutePageUrl}"`));
    assert.ok(text.includes(`property="og:url" content="${absolutePageUrl}"`));
    assert.ok(text.includes(`property="og:image" content="${originFixture}/og"`));
    assert.ok(text.includes(`name="twitter:image" content="${originFixture}/og"`));
    if (route === "/") assert.match(text, /application\/ld\+json/);
  }

  const robots = await readText(baseUrl, "/robots.txt");
  assert.match(robots.text, new RegExp(`Sitemap: ${originFixture}/sitemap\\.xml`));

  const sitemap = await readText(baseUrl, "/sitemap.xml");
  const locations = [...sitemap.text.matchAll(/<loc>(.*?)<\/loc>/g)].map((match) => match[1]);
  assert.deepEqual(locations, pages.map(([route]) => new URL(route, originFixture).toString()));
  assert.doesNotMatch(sitemap.text, /dashboard|admin|resumes|matching|\/api\//i);
  await verifyOgImage(baseUrl);
  await verifyBrandIcon(baseUrl);
  await readText(baseUrl, "/khong-ton-tai", 404);
}

const offEnvironment = { SITE_URL: originFixture, SEO_INDEXING_ENABLED: "false" };
build(offEnvironment);
await withProductionServer(3210, offEnvironment, verifyOff);

const onEnvironment = { SITE_URL: originFixture, SEO_INDEXING_ENABLED: "true" };
build(onEnvironment);
await withProductionServer(3211, onEnvironment, verifyOn);

console.log("Production HTTP SEO smoke OFF/ON: PASS");
