import { clock } from "../format.js";

// The night, as a timeline from 00:00 to 11:00. Each lane is one way of working the same ticket.
// events: [{ at: "2026-10-18 02:13:00", label, kind }] with kind in ticket | hold | lost | stopped | desk | onward
const START = 0;
const END = 11 * 60;

function x(ts) {
  const m = /[ T](\d{2}):(\d{2})/.exec(String(ts || ""));
  if (!m) return null;
  const minute = Number(m[1]) * 60 + Number(m[2]);
  return Math.max(0, Math.min(100, (100 * (minute - START)) / (END - START)));
}

export default function NightStrip({ lanes, deskOpens = "10:00" }) {
  const [dh, dm] = deskOpens.split(":").map(Number);
  const desk = (100 * (dh * 60 + dm - START)) / (END - START);
  const hours = Array.from({ length: 12 }, (_, h) => h);
  return (
    <div className="night" role="img" aria-label={lanes.map((l) => `${l.name}: ` + l.events.map((e) => `${clock(e.at)} ${e.label}`).join(", ")).join(". ")}>
      <div className="night-scale" aria-hidden="true">
        <span className="night-dark" style={{ width: `${desk}%` }} />
        {hours.map((h) => (
          <span key={h} className="night-hour" style={{ left: `${(100 * h * 60) / (END - START)}%` }}>{String(h).padStart(2, "0")}:00</span>
        ))}
        <span className="night-desk" style={{ left: `${desk}%` }}>desk opens</span>
      </div>
      {lanes.map((lane) => (
        <div key={lane.name} className="night-lane" aria-hidden="true">
          <span className="night-lane-name">{lane.name}</span>
          <div className="night-lane-track">
            {lane.events.filter((e) => x(e.at) !== null).map((e, i) => (
              <span key={i} className={`night-event ev-${e.kind}`} style={{ left: `${x(e.at)}%` }}>
                <span className="night-dot" />
                <span className={`night-label ${i % 2 ? "low" : ""}`}><b>{clock(e.at)}</b> {e.label}</span>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}