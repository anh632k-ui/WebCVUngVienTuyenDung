import { ImageResponse } from "next/og";
import { OG_IMAGE_SIZE, OgImageArtwork } from "@/components/marketing/og-image";

export function GET() {
  return new ImageResponse(OgImageArtwork(), OG_IMAGE_SIZE);
}
