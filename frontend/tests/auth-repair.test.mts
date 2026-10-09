import assert from "node:assert/strict";
import test from "node:test";
import { parseBackendOrigin } from "../src/lib/auth/backend-config.ts";
import { classifySessionFailure, sessionFailurePath } from "../src/lib/auth/session-errors.ts";

test("BACKEND_API_URL chỉ chấp nhận HTTP/HTTPS origin thuần", () => {
  assert.equal(parseBackendOrigin("https://api.example.test"), "https://api.example.test");
  assert.equal(parseBackendOrigin("http://localhost:8000/"), "http://localhost:8000");
  assert.equal(parseBackendOrigin(undefined), "http://127.0.0.1:8000");
  for (const value of [
    "ftp://api.example.test",
    "https://user:secret@api.example.test",
    "https://api.example.test/api",
    "https://api.example.test/api/..",
    "https://api.example.test?target=other",
    "https://api.example.test#fragment",
    "not-a-url",
  ]) assert.throws(() => parseBackendOrigin(value));
});

test("phân loại lỗi phiên không đánh đồng lỗi upstream với token hết hạn", () => {
  assert.equal(classifySessionFailure(401), "unauthenticated");
  assert.equal(classifySessionFailure(403), "inactive");
  assert.equal(classifySessionFailure(503), "unavailable");
  assert.equal(classifySessionFailure(502), "upstream");
  assert.equal(classifySessionFailure(500), "upstream");
  assert.equal(sessionFailurePath(401), "/dang-nhap?reason=expired");
  assert.equal(sessionFailurePath(403), "/trang-thai-phien?reason=inactive");
  assert.equal(sessionFailurePath(503), "/trang-thai-phien?reason=unavailable");
  assert.equal(sessionFailurePath(502), "/trang-thai-phien?reason=upstream");
});
