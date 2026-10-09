export function parseBackendOrigin(value: string | undefined) {
  const raw = value?.trim() || "http://127.0.0.1:8000";
  if (!/^https?:\/\/[^/?#]+\/?$/i.test(raw)) throw new Error("Invalid backend origin configuration");
  const url = new URL(raw);
  if (
    (url.protocol !== "http:" && url.protocol !== "https:")
    || url.username
    || url.password
    || url.pathname !== "/"
    || url.search
    || url.hash
  ) {
    throw new Error("Invalid backend origin configuration");
  }
  return url.origin;
}
