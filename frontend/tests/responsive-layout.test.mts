import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");

test("mobile hero starts the highlighted phrase on a new line", () => {
  assert.match(css, /\.hero h1 span\s*\{[^}]*display:\s*block;/);
});

test("mobile dashboard navigation is constrained to its own horizontal scroller", () => {
  assert.match(css, /\.dashboard-sidebar\s*\{[^}]*max-width:\s*100%;[^}]*min-width:\s*0;/);
  assert.match(css, /\.dashboard-sidebar \.dashboard-nav\s*\{[^}]*max-width:\s*100%;[^}]*min-width:\s*0;[^}]*overflow-x:\s*auto;[^}]*width:\s*100%;/);
});
