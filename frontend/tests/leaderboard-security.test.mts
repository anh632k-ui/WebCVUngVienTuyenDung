import assert from "node:assert/strict";
import test from "node:test";
import { validateLeaderboardQuery } from "../src/lib/leaderboard/contracts.ts";
test("leaderboard page and min_score validated", () => {
  assert.equal(validateLeaderboardQuery(new URLSearchParams("page=1&limit=20&min_score=80.5"))?.get("min_score"), "80.5");
  assert.equal(validateLeaderboardQuery(new URLSearchParams("min_score=0"))?.get("min_score"), "0");
  assert.equal(validateLeaderboardQuery(new URLSearchParams("page=0")), null);
  assert.equal(validateLeaderboardQuery(new URLSearchParams("min_score=101")), null);
  assert.equal(validateLeaderboardQuery(new URLSearchParams("min_score=NaN")), null);
  assert.equal(validateLeaderboardQuery(new URLSearchParams("min_score=-1")), null);
});
