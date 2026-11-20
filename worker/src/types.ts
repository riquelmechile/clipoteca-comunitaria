export type AspectRatio = "9:16" | "16:9" | "1:1" | "other";
export type Orientation = "vertical" | "horizontal" | "square" | "other";
export type ClipStatus = "published";

export interface Clip {
  id: string;
  title: { es: string; en: string };
  description: { es: string; en: string };
  tags: { es: string[]; en: string[] };
  source: { provider: string; provider_id: string; source_language: string; source_title: string; source_url: string };
  media: { duration_seconds: number; width: number; height: number; fps: number; aspect_ratio: AspectRatio; orientation: Orientation; codec: string; container: string; has_audio: boolean; size_bytes: number; sha256: string; perceptual_hash: string };
  storage: { provider: "github_releases"; asset_url: string; thumbnail_url: string };
  status: ClipStatus;
  created_at: string;
}

