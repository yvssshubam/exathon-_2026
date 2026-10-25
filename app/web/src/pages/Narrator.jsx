import { useState } from "react";
import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, inr, when } from "../format.js";
import { useLoad } from "../hooks.js";

// The narrator's view (Module 8): the whole trail across every bank, for the demo. It reads every bank's schema as
// the admin. No bank's agent, checker or officer can: each bank's database users see only that bank's schema.
const VICTIMS = [["V_LAKSHMI", "Lakshmi (victim 1)"], ["V_RAVI", "Ravi (victim 2)"], ["V_MEENA", "Meena (victim 3)"]];
const rs = (s) => String(s || "").replace(/Rs (?=\d)/g, "₹");

export default function Narrator() {
  const [victim, setVictim] = useState("V_LAKSHMI");
  const { data, error, loading, reload } = useLoad(() => api.narrator(victim), [victim]);
  if (loading && !data) return <div className="wrap page"><Loading what="Loading every bank" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  const t = data.trail;
  return (
    <main id="main" className="wrap page narrator">
      <p className="caption" role="note">{data.caption}</p>
      <div className="pagehead">
        <div>
          <h1>The whole night</h1>
          <p className="lede">Three banks, one simulated clock, the portal as the only link. Bank X and Bank Y run the agent; Bank Z's desk opens at 10:00. Clock: {data.clock ? when(data.clock) : "not started"}.</p>
        </div>
        <label className="officer">Victim
          <select value={victim} onChange={(e) => setVictim(e.target.value)}>
            {VICTIMS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
      </div>

      <section className="lanes" aria-label="Where the victim's money is, per bank">
        {t.banks.map((b) => (
          <div key={b.bank_id} className="card pad lanecard">
            <div className="lane-h"><b>{b.name}</b><span className={`pill ${b.works_tickets === "desk" ? "pill-plain" : "pill-held"}`}>{b.works_tickets === "desk" ? "human desk" : "agent"}</span></div>
            <div className="big">{inr(b.victim_held_inr)}</div>
            <div className="sub">of the victim's money held here{b.tickets_waiting ? ` · ${b.tickets_waiting} ticket waiting` : ""}</div>
            {b.accounts.length > 0 && (
              <ul className="history">
                {b.accounts.map((a) => (
                  <li key={a.case_id + a.account_id}>{a.is_exit ? <span>{a.name}: {inr(a.victim_inr)} gone</span> : <span>{a.name}: {inr(a.held_inr)} held of {inr(a.victim_inr)}</span>} <span className="muted">({a.case_id})</span></li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </section>
      <p className="note" style={{ marginBottom: 24 }}>In all: {inr(t.victim_held_inr)} of this victim's money held across the banks.</p>

      <section className="recsec">
        <h2>What happened, in order</h2>
        <ol className="timeline">
          {data.timeline.map((e, i) => (
            <li key={i} className={`tl-${e.kind}`}>
              <span className="at">{clock(e.at)}</span>
              <span className="who">{e.bank ? e.bank.replace("BANK_", "Bank ") : "Portal"}</span>
              <span>{rs(e.text)}</span>
            </li>
          ))}
        </ol>
        {data.timeline.length === 0 && <p className="empty">Nothing yet: replay the night from This morning.</p>}
      </section>
    </main>
  );
}