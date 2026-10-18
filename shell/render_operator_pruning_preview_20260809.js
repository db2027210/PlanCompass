const fs = require("fs");
const path = require("path");

const outDir = process.argv[2] || "/tmp/operator_ppt_render";
fs.mkdirSync(outDir, { recursive: true });

const C = {
  navy: "#111111", navy2: "#E9E9E9", teal: "#333333", tealLight: "#F5F5F5",
  orange: "#555555", orangeLight: "#F2F2F2", sand: "#FFFFFF", white: "#FFFFFF",
  ink: "#111111", muted: "#555555", line: "#C8C8C8", pale: "#F7F7F7", red: "#111111",
};
const esc = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const text = (x, y, value, size, color, weight = 400, anchor = "start", family = "WenQuanYi Micro Hei") =>
  `<text x="${x}" y="${y}" font-family="${family}" font-size="${size}" font-weight="${weight}" fill="${color}" text-anchor="${anchor}">${esc(value)}</text>`;
const lines = (x, y, values, size, color, weight = 400, gap = size * 1.26, anchor = "start", family = "WenQuanYi Micro Hei") =>
  values.map((value, i) => text(x, y + i * gap, value, size, color, weight, anchor, family)).join("\n");
const rect = (x, y, w, h, fill, stroke = fill, radius = 10) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="1.2"/>`;

function frame(body) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
  <rect width="1600" height="900" fill="${C.sand}"/>
  ${body}
  </svg>`;
}

function slide1() {
  let s = "";
  s += text(66, 75, "“”", 34, C.navy, 700);
  s += text(68, 112, "20260809_sf5_formal_affinity0_255_v2 ·  = outer_candidate_decision.accepted", 14, C.muted);
  s += text(1535, 74, "01 / 02", 15, C.teal, 700, "end");
  const cards = [
    [68, 142, 305, C.white, "29", " Outer ", C.navy],
    [397, 142, 305, C.white, "0", " Accept", C.red],
    [725, 142, 384, C.white, "28", " DB QPS ", C.teal],
    [1132, 142, 400, C.white, "1", "", C.orange],
  ];
  for (const [x, y, w, fill, value, label, color] of cards) {
    s += rect(x, y, w, 86, C.white, C.line, 0);
    s += text(x + 20, y + 55, value, 34, color, 700);
    s += text(x + 120, y + 51, label, 16, C.ink, 700);
  }

  const x = [68, 222, 440, 606, 822, 1068, 1532];
  const y = [253, 320, 414, 515, 616, 717];
  s += `<rect x="68" y="253" width="1464" height="464" fill="${C.white}" stroke="${C.line}"/>`;
  s += `<rect x="68" y="253" width="1464" height="67" fill="${C.navy2}"/>`;
  for (const xv of x.slice(1, -1)) s += `<line x1="${xv}" y1="253" x2="${xv}" y2="717" stroke="${C.line}"/>`;
  for (const yv of y.slice(1, -1)) s += `<line x1="68" y1="${yv}" x2="1532" y2="${yv}" stroke="${C.line}"/>`;
  const centers = x.slice(0, -1).map((v, i) => (v + x[i + 1]) / 2);
  ["", "", "Outer Accept / Reject", "DB QPS  incumbent", "", "Reason / "].forEach((v, i) => s += text(centers[i], 294, v, 15, C.ink, 700, "middle"));

  const rowCenters = [366, 464, 565, 666];
  const rows = [
    [["JOB", "Memory"], [""], ["0 / 3"], ["−0.45%  +0.55%"], ["Memory ↓", "0.010%0.016%"], ["2× QPS 1× ", " 3 "]],
    [["TPC-H SF5", "Memory"], ["IndexScan ↑", "SeqScan ↓"], ["0 / 12"], ["−31.66%  −25.28%"], ["−0.150%  +0.175%", ""], ["12× QPS ", " 1 "]],
    [["HyBench XP", "CPU"], ["HashAgg ↓"], ["0 / 7"], ["−32.91%  −31.45%"], ["CPU ↓", "19.07%19.22%"], ["7× QPS ", " 7 "]],
    [["HyBench XP", "IO"], ["HashAgg ↓"], ["0 / 7"], ["−11.21%  −7.27%"], ["IO−1.70%  +7.19%", ""], ["7× QPS ", " 7 "]],
  ];
  rows.forEach((row, r) => row.forEach((cell, c) => {
    const color = c === 2 || (c === 3 && r >= 2) ? C.red : (c === 4 && r === 2 ? C.teal : C.ink);
    const weight = c === 0 || c === 2 || (r >= 2 && c === 1) ? 700 : 400;
    const gap = 19;
    const startY = rowCenters[r] - ((cell.length - 1) * gap) / 2 + 5;
    s += lines(centers[c], startY, cell, c === 5 ? 13.5 : 14.5, color, weight, gap, "middle");
  }));

  s += rect(68, 747, 1464, 94, C.white, "#777777", 0);
  s += text(104, 805, "  ≠  ", 28, C.ink, 700);
  s += text(1485, 802, " Outer accept/rejectQPS Reason", 16, C.ink, 400, "end");
  s += text(104, 830, "", 10.5, C.muted);
  return frame(s);
}

function planNode(x, y, w, label, style) {
  const map = {
    normal: [C.white, "#888888", C.ink], hash: ["#F2F2F2", "#444444", C.ink],
    group: ["#F7F7F7", "#444444", C.ink], scan: [C.white, "#888888", C.ink],
  };
  const [fill, stroke, color] = map[style];
  return rect(x, y, w, 50, fill, stroke, 9) + text(x + w / 2, y + 32, label, 15, color, style === "normal" || style === "scan" ? 400 : 700, "middle", "DejaVu Sans Mono");
}
function arrow(x, y) {
  return `<path d="M ${x} ${y} L ${x + 12} ${y + 16} L ${x + 24} ${y} Z" fill="#777777"/>`;
}

function slide2() {
  let s = "";
  s += text(66, 75, " HashAgg ", 34, C.navy, 700);
  s += text(68, 112, " GROUP BY PostgreSQL ", 14, C.muted);
  s += text(1535, 74, "02 / 02", 15, C.teal, 700, "end");
  s += rect(68, 142, 1464, 86, C.white, "#777777", 0);
  s += text(800, 195, "SELECT count(*) FROM (SELECT sourceid, count(*) FROM transfer GROUP BY sourceid) t;", 18, C.ink, 400, "middle", "DejaVu Sans Mono");
  s += text(280, 280, "", 22, C.navy, 700, "middle");
  s += text(735, 280, "", 22, C.navy, 700, "middle");
  const base = [["Aggregate", "normal"], ["Finalize GroupAggregate", "group"], ["Gather Merge", "normal"], ["Sort", "normal"], ["Partial HashAggregate", "hash"], ["Parallel Seq Scan", "scan"]];
  base.forEach(([label, style], i) => { const y = 300 + i * 68; s += planNode(94, y, 374, label, style); if (i < base.length - 1) s += arrow(268, y + 52); });
  const cand = [["Aggregate", "normal"], ["GroupAggregate", "group"], ["Gather Merge", "normal"], ["Sort", "normal"], ["Parallel Seq Scan", "scan"]];
  cand.forEach(([label, style], i) => { const y = 300 + i * 80; s += planNode(548, y, 374, label, style); if (i < cand.length - 1) s += arrow(722, y + 52); });
  s += `<path d="M 477 422 L 535 466 L 477 510 Z" fill="${C.orange}"/>`;
  s += text(506, 540, "", 13, C.orange, 700, "middle");

  s += rect(973, 250, 559, 446, C.white, C.line, 12);
  s += text(1008, 302, " HashAgg ↓", 22, C.navy, 700);
  const bullets = ["GroupAggregate ", " Partial / Finalize ", "Sort ", "“”"];
  bullets.forEach((v, i) => { s += `<circle cx="1015" cy="${360 + i * 54}" r="5" fill="${C.teal}"/>`; s += text(1035, 367 + i * 54, v, 17, C.ink); });
  s += rect(1008, 586, 489, 74, "#F5F5F5", "#777777", 10);
  s += text(1253, 632, " GROUP BY ", 18, C.ink, 700, "middle");

  s += rect(68, 742, 1464, 110, C.white, "#777777", 0);
  s += text(104, 805, "", 22, C.ink, 700);
  s += text(725, 801, " + workload contract +  + acceptance policy + incumbent ", 17, C.ink, 700, "middle");
  s += `<path d="M 1210 775 L 1255 797 L 1210 819 Z" fill="${C.orange}"/>`;
  s += text(1390, 804, "Outer accept / reject", 17, C.ink, 700, "middle", "DejaVu Sans");
  s += text(104, 833, "GroupAggregate /  / worker", 10.5, C.muted);
  return frame(s);
}

fs.writeFileSync(path.join(outDir, "slide-01.svg"), slide1());
fs.writeFileSync(path.join(outDir, "slide-02.svg"), slide2());
