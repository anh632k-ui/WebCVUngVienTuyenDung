import { NextResponse } from "next/server";
import { validateMutationRequest } from "./security.ts";

export const NO_STORE_HEADERS = { "Cache-Control": "no-store, max-age=0" };

export function jsonNoStore(body: unknown, status = 200) {
  return NextResponse.json(body, { status, headers: NO_STORE_HEADERS });
}

export function rejectUnsafeMutation(request: Request) {
  const message = validateMutationRequest(request);
  return message
    ? jsonNoStore({ success: false, error: { code: "CSRF_REJECTED", message } }, 403)
    : null;
}

export async function readJson(request: Request) {
  try { return await request.json() as unknown; } catch { return null; }
}
