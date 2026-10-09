export const OG_IMAGE_SIZE = { width: 1200, height: 630 } as const;

export function OgImageArtwork() {
  return (
    <div style={{ alignItems: "center", background: "#f8fafc", color: "#0f172a", display: "flex", fontFamily: "Arial, sans-serif", height: "100%", justifyContent: "center", padding: "72px", width: "100%" }}>
      <div style={{ background: "white", border: "2px solid #e2e8f0", borderRadius: "40px", display: "flex", flexDirection: "column", height: "100%", justifyContent: "space-between", padding: "64px", width: "100%" }}>
        <div style={{ alignItems: "center", display: "flex", fontSize: 34, fontWeight: 700 }}>
          <div style={{ background: "#2563eb", borderRadius: 14, color: "white", display: "flex", marginRight: 18, padding: "10px 14px" }}>AI</div>
          <div style={{ display: "flex" }}>CVInsight</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ color: "#2563eb", display: "flex", fontSize: 22, fontWeight: 700, letterSpacing: 2, marginBottom: 20 }}>AI + NLP CHO HỒ SƠ NGHỀ NGHIỆP</div>
          <div style={{ display: "flex", fontSize: 60, fontWeight: 700, letterSpacing: -2, lineHeight: 1.12, maxWidth: 950 }}>Hiểu năng lực. Khám phá mức độ phù hợp.</div>
        </div>
        <div style={{ color: "#475569", display: "flex", fontSize: 24 }}>Phân tích CV · Đối chiếu JD · Nhận diện Skill Gap</div>
      </div>
    </div>
  );
}
