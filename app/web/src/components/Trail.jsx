import { inr, shortAccount } from "../format.js";

// The money trail, drawn in columns by hop: the victim on the left, money flowing right. Own-bank accounts are
// solid, other banks dashed, exits hatched. Edge labels say how much of each payment was the victim's money.
const COL = 210;
const ROW = 108;
const W = 168;
const H = 76;

export default function Trail({ trail, selected, onSelect }) {
  const byHop = {};
  for (const n of trail.nodes) {
    const hop = n.hop ?? 0;
    (byHop[hop] ||= []).push(n);
  }
  const pos = {};
  let rows = 1;
  for (const [hop, list] of Object.entries(byHop)) {
    list.sort((a, b) => (a.kind === "exit") - (b.kind === "exit") || a.id.localeCompare(b.id));
    list.forEach((n, i) => { pos[n.id] = { x: 12 + Number(hop) * COL, y: 12 + i * ROW }; });
    rows = Math.max(rows, list.length);
  }
  const width = 12 + (Math.max(...Object.keys(byHop).map(Number)) + 1) * COL;
  const height = 24 + rows * ROW;
  return (
    <div className="trail-wrap">
      <svg className="trail" viewBox={`0 0 ${width} ${height}`} width={width} height={height}
           role="group" aria-label="Money trail: click an account to see its split and actions">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="arrowhead" />
          </marker>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" className="hatch-line" />
          </pattern>
        </defs>
        {trail.edges.map((e) => {
          const a = pos[e.from_account];
          const b = pos[e.to_account];
          if (!a || !b) return null;
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 4, y2 = b.y + H / 2;
          const mx = (x1 + x2) / 2;
          return (
            <g key={e.txn_id} className={e.is_cycle ? "edge loop" : "edge"}>
              <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} markerEnd="url(#arrow)" />
              <text x={mx} y={(y1 + y2) / 2 - 6} textAnchor="middle" className="edge-label">
                {inr(e.victim_inr)}{e.victim_inr < e.amount_inr ? ` of ${inr(e.amount_inr)}` : ""}
              </text>
            </g>
          );
        })}
        {trail.nodes.map((n) => {
          const p = pos[n.id];
          const clickable = n.kind !== "victim";
          const select = () => clickable && onSelect(n.id);
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`} className={`node node-${n.kind} ${selected === n.id ? "on" : ""} ${n.waiting?.length ? "has-waiting" : ""}`}
               tabIndex={clickable ? 0 : -1} role={clickable ? "button" : undefined}
               aria-label={clickable ? `${n.label}, ${n.done || "no action"}` : "Victim"}
               onClick={select} onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && select()}>
              <rect width={W} height={H} rx="6" />
              {n.kind === "exit" && <rect width={W} height={H} rx="6" fill="url(#hatch)" />}
              <text x="10" y="20" className="node-title">{n.kind === "victim" ? "Victim (Bank Z)" : trunc(n.label, 22)}</text>
              <text x="10" y="37" className="node-sub">
                {n.kind === "exit" ? "left banking" : n.kind === "victim" ? "paid by IMPS" : `${n.bank_name}, ${shortAccount(n.id)}`}
              </text>
              {n.kind !== "victim" && <text x="10" y="56" className="node-done">{trunc(n.done || "no action here", 26)}</text>}
              {n.waiting?.length > 0 && <text x="10" y="70" className="node-waiting">{n.waiting.length} waiting for you</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function trunc(s, n) {
  s = String(s || "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}