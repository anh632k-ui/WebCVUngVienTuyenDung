import { Icon } from "./icons";

const dimensions = [
  { label: "Kỹ năng", value: 92, style: "skill" },
  { label: "Ngữ nghĩa", value: 84, style: "semantic" },
  { label: "Kinh nghiệm", value: 76, style: "experience" },
];

export function ScorePreview() {
  return (
    <div className="preview-frame" aria-label="Dữ liệu minh họa cho giao diện đánh giá mức độ phù hợp">
      <div className="preview-window">
        <div className="preview-window-bar">
          <span className="window-dots" aria-hidden="true"><i /><i /><i /></span>
          <span className="preview-window-title">Tổng quan kết quả</span>
          <span className="preview-demo-tag">DỮ LIỆU MINH HỌA</span>
        </div>
        <div className="preview-content">
          <div className="preview-heading">
            <div><span className="preview-eyebrow">PHÂN TÍCH TƯƠNG THÍCH</span><h2>Backend Developer</h2><p>CV mẫu · Vị trí mẫu</p></div>
            <div className="preview-status"><span /> Minh họa</div>
          </div>
          <div className="preview-analysis">
            <div className="preview-score">
              <div className="score-dial"><div className="score-dial-center"><strong>86<span>/100</span></strong><small>Điểm tổng hợp</small></div></div>
              <div className="preview-score-caption"><Icon name="sparkles" size={15} /> Không phải benchmark</div>
            </div>
            <div className="preview-breakdown">
              <span className="preview-section-label">Điểm thành phần minh họa</span>
              {dimensions.map((item) => (
                <div className="metric-row" key={item.label}>
                  <div className="metric-label"><span>{item.label}</span><strong>{item.value}/100</strong></div>
                  <div className="metric-track"><span className={`metric-fill ${item.style}`} style={{ width: `${item.value}%` }} /></div>
                </div>
              ))}
            </div>
          </div>
          <p className="preview-formula">Điểm tổng = Kỹ năng × trọng số + Ngữ nghĩa × trọng số + Kinh nghiệm × trọng số. Trọng số do từng JD quy định và có tổng bằng 100%.</p>
          <div className="preview-skills">
            <div className="preview-skill-panel"><div className="preview-skill-heading"><Icon name="check" size={16} /> Kỹ năng phù hợp</div><div className="chip-group"><span className="chip matched">Python</span><span className="chip matched">FastAPI</span><span className="chip matched">PostgreSQL</span></div></div>
            <div className="preview-skill-panel"><div className="preview-skill-heading"><Icon name="target" size={16} /> Skill Gap</div><div className="chip-group"><span className="chip improvement">Docker</span><span className="chip improvement">CI/CD</span></div></div>
          </div>
        </div>
      </div>
    </div>
  );
}
