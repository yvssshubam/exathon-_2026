import { useState } from "react";
import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, duration, hoursRange, inr, inrR, plural } from "../format.js";
import { useLoad } from "../hooks.js";

const GREY = "#4F5966", VIOLET = "#5B3FA0", GREEN = "#2F7A5A";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const short = (name) => String(name || "").replace(/\s*\(.*\)$/, "");
const joinAnd = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);
const ms = (ts) => new Date(String(ts).replace(" ", "T")).getTime();
const dayOf = (ts) => `${Number(String(ts).slice(8, 10))} ${MONTHS[Number(String(ts).slice(5, 7)) - 1]}`;
const isNight = (ts) => { const h = Number(String(ts).slice(11, 13)); return h < 10 || h >= 18; };

// Today's manual process vs the agent, on the same ticket and the same transactions, computed in Exasol.
export default function Compare({ caseId }) {
  const [tab, setTab] = useState("ticket");
  const cmp = useLoad(() => api.compare(caseId, true), [caseId]);
  const acts = useLoad(() => api.actions(caseId), [caseId]);
  const day = useLoad(() => (tab === "day" ? api.day() : Promise.resolve(null)), [tab]);

  if (cmp.loading && !cmp.data) return <div className="wrap page"><Loading what="Replaying today's manual process in Exasol" /></div>;
  if (cmp.error) return <div className="wrap page"><Failed error={cmp.error} retry={cmp.reload} /></div>;
  const d = cmp.data;
  const worked = !!d.tool.first_hold_at || d.tool.holds_overnight.length > 0;

  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>{tab === "day" ? "The whole day, worked two ways" : `The ${clock(d.received_at)} ticket, worked two ways`}</h1>
          <p className="lede">
            {tab === "day"
              ? "Every ticket the agent has worked, replayed through today's manual process on the same transactions."
              : `Same fraud, handled two ways. Without the agent, staff start at 10:00 and freeze whole accounts. With it, holds go on${d.tool.first_hold_at ? ` at ${clock(d.tool.first_hold_at)}` : ""} and cover only the victim's share.`}
          </p>
        </div>
        <div className="tabs" role="tablist" aria-label="Scope">
          <button role="tab" aria-selected={tab === "ticket"} onClick={() => setTab("ticket")}>This ticket</button>
          <button role="tab" aria-selected={tab === "day"} onClick={() => setTab("day")}>Whole day{day.data ? `, ${day.data.worked} tickets` : ""}</button>
        </div>
      </div>

      {tab === "ticket" && (
        !worked
          ? <p className="empty">The agent has not worked this ticket yet. <a href={`#/case/${caseId}`}>Open the ticket</a> and let it work it.</p>
          : <Ticket d={d} actions={acts.data || []} />
      )}
      {tab === "day" && <Day day={day} />}

      <p className="note">"Without the agent" is the bank's manual process today, replayed on the same {tab === "day" ? "tickets" : "ticket"}: staff start at 10:00, take about {d.desk.first_lookup_minutes} minutes to trace the first account and {d.desk.per_account_minutes} for each next one, and freeze whole accounts.{tab === "ticket" && d.today.accounts.length ? ` The victim reported at ${clock(d.received_at)} in both versions.` : ""}</p>
    </main>
  );
}

function Pair({ label, today, todayNote, agent, agentNote }) {
  return (
    <div className="card pair">
      <div className="label">{label}</div>
      <div className="two">
        <div><div className="side-h">Without the agent</div><div className="v today">{today}</div><div className="n">{todayNote}</div></div>
        <div><div className="side-h agent">With the agent</div><div className="v">{agent}</div><div className="n">{agentNote}</div></div>
      </div>
    </div>
  );
}

function Ticket({ d, actions }) {
  const narrow = actions.filter((a) => a.action_code === "PROPOSE_NARROW");
  const pending = narrow.filter((a) => ["PROPOSED", "WAITING"].includes(a.action_status));
  const release = pending.reduce((s, a) => s + (a.current_hold_inr - a.amount_inr), 0);
  const frozenWhole = d.today.accounts.filter((a) => a.action === "WHOLE_FREEZE" && a.honest_locked_inr > 0);
  const emptied = [...new Set(d.walked_out_after_ticket.map((w) => short(w.name)))].sort();
  const lastWalk = d.walked_out_after_ticket.at(-1)?.at;
  const pct = d.victim_paid_inr ? Math.round((d.tool.victim_held_inr / d.victim_paid_inr) * 100) : 0;
  let honestNote = d.tool.honest_locked_overnight_inr ? "Only the receiver's own money that came with the victim's" : "Nothing but the victim's money";
  if (pending.length) honestNote = `${inr(Math.max(0, d.tool.honest_locked_overnight_inr - release))} once you release ${joinAnd(pending.map((a) => `${short(a.name)}'s extra ${inr(a.current_hold_inr - a.amount_inr)}`))}`;
  else if (d.tool.honest_locked_now_inr < d.tool.honest_locked_overnight_inr) honestNote = `${inr(d.tool.honest_locked_now_inr)} now: you released the rest`;

  return (
    <>
      <div className="pairs">
        <Pair label="Victim's money held" today={inrR(d.today.victim_held_inr)}
          todayNote={emptied.length ? `${joinAnd(emptied)} emptied by ${clock(lastWalk)}` : d.today.first_hold_at ? `Held at ${clock(d.today.first_hold_at)}` : "Nothing held"}
          agent={inrR(d.tool.victim_held_inr)} agentNote={`${pct}% of the ${inr(d.victim_paid_inr)} reported`} />
        <Pair label="Innocent customers' money frozen" today={inrR(d.today.honest_locked_inr)}
          todayNote={frozenWhole.length ? `${joinAnd(frozenWhole.map((a) => `${short(a.name)}'s`))} whole account${frozenWhole.length > 1 ? "s" : ""}` : "No whole-account freezes"}
          agent={inrR(d.tool.honest_locked_overnight_inr)} agentNote={honestNote} />
        <Pair label="First hold" today={d.today.first_hold_at ? clock(d.today.first_hold_at) : "–"}
          todayNote={d.today.minutes_to_first_hold != null ? `${duration(d.today.minutes_to_first_hold)} after the ticket` : ""}
          agent={d.tool.first_hold_at ? clock(d.tool.first_hold_at) : "–"}
          agentNote={d.tool.minutes_to_first_hold != null ? `${duration(d.tool.minutes_to_first_hold)} after the ticket` : ""} />
      </div>
      <Timeline d={d} pending={pending} narrow={narrow} actions={actions} />
    </>
  );
}

// Two lanes of numbered events on one time axis: today's manual process and the agent.
function Timeline({ d, pending, narrow, actions }) {
  const heldOn = {};
  for (const h of d.tool.holds_overnight) if (h.kind !== "MARKER") heldOn[h.account_id] = Math.max(heldOn[h.account_id] || 0, h.amount_inr);
  const bankNames = [...new Set(actions.filter((a) => a.action_code === "ONWARD_ENTRY").map((a) => a.bank_name).filter(Boolean))];
  const banks = bankNames.length ? joinAnd(bankNames) : "the other bank";

  const without = [];
  for (const w of d.walked_out_after_ticket) {
    const crypto = /crypto/i.test(w.where);
    without.push({ at: w.at, text: crypto ? `${short(w.name)} sends ${inr(w.amount_inr)} to a crypto exchange` : `${short(w.name)} withdraws ${inr(w.amount_inr)} at ${w.where.replace(/^ATM\s+\S+,\s*/, "ATM ")}` });
  }
  for (const a of d.today.accounts) {
    const text = a.action === "WHOLE_FREEZE"
      ? `${short(a.name)} frozen whole: ${inr(a.balance_then_inr)}, of which ${inr(a.victim_held_inr)} is the victim's`
      : a.action === "NOTHING_LEFT" ? `Staff check ${short(a.name)}: empty` : `Staff hold ${inr(a.victim_held_inr)} on ${short(a.name)}`;
    without.push({ at: a.action_at, text });
  }
  if (d.today.onward_filed_at) without.push({ at: d.today.onward_filed_at, text: `Onward entry typed: ${banks} alerted` });

  const agent = [];
  const holds = Object.entries(heldOn).map(([acc, amt]) => `${inr(amt)} on ${short(d.tool.holds_overnight.find((h) => h.account_id === acc).name)}`);
  if (d.tool.first_hold_at) {
    agent.push({ at: d.tool.first_hold_at, text: `${holds.length ? `Holds ${joinAnd(holds)}.` : "Nothing left to hold; paperwork filed."}${d.tool.onward_filed_at ? ` ${banks} alerted.` : ""}` });
  }
  for (const w of d.walked_out_after_ticket) {
    const held = heldOn[w.account_id] || 0;
    const kind = /crypto/i.test(w.where) ? "crypto transfer" : "ATM withdrawal";
    agent.push({ at: w.at, text: held >= w.amount_inr ? `${short(w.name)}'s ${kind} refused: the money is held` : held > 0 ? `${short(w.name)}'s ${kind} stopped at ${inr(w.amount_inr - held)}: ${inr(held)} is held` : `${short(w.name)}'s ${kind} goes through: nothing of the victim's was left there` });
  }
  const opens = d.desk.desk_opens;
  if (narrow.length && isNight(d.received_at)) {
    const n = narrow[0], who = short(n.name);
    const now = d.tool.holds_now.find((h) => h.account_id === n.account_id && h.kind === "LIEN");
    let text;
    if (pending.length) text = `Desk opens. You decide: cut ${who}'s hold to ${inr(n.amount_inr)}, and ${who} keeps ${inr(Math.max(0, n.balance_now_inr - n.amount_inr))}`;
    else if (n.action_status === "REJECTED") text = `Desk opens. You keep ${who}'s hold at ${inr(n.current_hold_inr)}`;
    else text = `Desk opens. You cut ${who}'s hold to ${inr(now ? now.amount_inr : n.amount_inr)}; ${who} keeps ${inr(Math.max(0, n.balance_now_inr - (now ? now.amount_inr : n.amount_inr)))}`;
    agent.push({ at: opens, desk: true, text });
  }

  const all = [d.received_at, ...without.map((e) => e.at), ...agent.map((e) => e.at)].filter(Boolean);
  const t0 = new Date(Math.min(...all.map(ms)));
  t0.setMinutes(0, 0, 0);
  if (isNight(d.received_at) && Number(String(d.received_at).slice(11, 13)) < 10) t0.setHours(0);
  const lastMs = Math.max(...all.map(ms)) + 40 * 60000;
  const t1 = new Date(lastMs);
  t1.setMinutes(t1.getMinutes() <= 30 ? 30 : 60, 0, 0);
  const span = t1 - t0;
  const x = (ts) => `${(((ms(ts) - t0) / span) * 100).toFixed(2)}%`;
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (dt) => `${pad(dt.getHours())}:${pad(dt.getMinutes())}`;

  // Desk-closed stretches (before 10:00, from 18:00) inside the window, as a background gradient.
  const stops = [];
  for (let t = t0.getTime(); t < t1.getTime(); t += 30 * 60000) {
    const h = new Date(t).getHours();
    stops.push([t, h < 10 || h >= 18]);
  }
  const grad = stops.map(([t, closed], i) => {
    const a = ((t - t0) / span) * 100, b = (((stops[i + 1]?.[0] ?? t1.getTime()) - t0) / span) * 100;
    return `${closed ? "#E3E8F5" : "#F7F9FA"} ${a.toFixed(2)}% ${b.toFixed(2)}%`;
  }).join(", ");

  const hours = span / 3600000;
  const step = Math.max(1, Math.ceil(hours / 7));
  const ticks = [];
  for (let t = new Date(t0); t <= t1; t = new Date(t.getTime() + step * 3600000)) ticks.push(t);
  if (ticks.at(-1).getTime() !== t1.getTime() && (t1 - ticks.at(-1)) > 0.4 * step * 3600000) ticks.push(t1);

  // Events in the same minute share one numbered marker, so markers never sit on top of each other.
  const sorted = (list, color) => {
    const merged = [];
    for (const e of list.sort((a, b) => ms(a.at) - ms(b.at))) {
      const last = merged.at(-1);
      if (last && last.at === e.at && !e.desk && !last.desk) last.text = `${last.text}. ${e.text}`;
      else merged.push({ ...e });
    }
    return merged.map((e, i) => ({ ...e, n: i + 1, bg: e.desk ? GREEN : color }));
  };
  const lanes = [{ name: "Without the agent", marks: sorted(without, GREY) }, { name: "With the agent", marks: sorted(agent, VIOLET) }];
  const sameDay = t0.getDate() === t1.getDate();

  return (
    <section className="card night" aria-labelledby="tl-h">
      <h2 id="tl-h">{isNight(d.received_at) ? "The night of " : ""}{dayOf(d.received_at)}, {hhmm(t0)} to {hhmm(t1)}{sameDay ? "" : " next day"}</h2>
      <div className="ticks" aria-hidden="true">
        {ticks.map((t) => <span key={t.getTime()} style={{ left: `${(((t - t0) / span) * 100).toFixed(2)}%` }}>{hhmm(t)}</span>)}
      </div>
      {lanes.map((l) => (
        <div className="lane" key={l.name}>
          <div className="lane-name">{l.name}</div>
          <div className="lane-track" style={{ background: `linear-gradient(90deg, ${grad})` }}>
            {l.marks.map((m) => <span key={m.n} className="mark" title={`${clock(m.at)}: ${m.text}`} style={{ left: x(m.at), background: m.bg }}>{m.n}</span>)}
          </div>
        </div>
      ))}
      <div className="legend">
        <span><span className="swatch" style={{ background: "#E3E8F5" }} />Desk closed</span>
        <span><span className="swatch" style={{ background: "#F7F9FA", border: "1px solid #D9DFE5" }} />Desk open from 10:00</span>
      </div>
      <div className="lists">
        {lanes.map((l) => (
          <div key={l.name}>
            <h3>{l.name}</h3>
            <ol>
              {l.marks.map((m) => (
                <li key={m.n}><span className="dot" style={{ background: m.bg }}>{m.n}</span><b>{clock(m.at)}</b><span>{m.text}</span></li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}

function Day({ day }) {
  if (day.loading && !day.data) return <Loading what="Replaying every worked ticket through today's manual process" />;
  if (day.error) return <Failed error={day.error} retry={day.reload} />;
  const d = day.data;
  if (!d || !d.worked) return <p className="empty">The agent hasn't worked any tickets yet. Use Replay the day on the Review page.</p>;
  const todayMinutes = d.tickets.map((t) => t.today_minutes).filter((m) => m != null);
  const pct = d.victim_paid_inr ? Math.round((d.tool.victim_held_inr / d.victim_paid_inr) * 100) : 0;
  return (
    <>
      <div className="pairs">
        <Pair label="Victims' money held" today={inrR(d.today.victim_held_inr)} todayNote={`of ${inr(d.victim_paid_inr)} reported`}
          agent={inrR(d.tool.victim_held_inr)} agentNote={`${pct}% of the money reported`} />
        <Pair label="Innocent customers' money frozen" today={inrR(d.today.honest_locked_inr)}
          todayNote={d.today.accounts_frozen_whole ? `${plural(d.today.accounts_frozen_whole, "account")} frozen in full` : "No whole-account freezes"}
          agent={inrR(d.tool.honest_locked_now_inr)}
          agentNote={d.tool.honest_locked_overnight_inr > d.tool.honest_locked_now_inr ? `${inr(d.tool.honest_locked_overnight_inr)} before you released the extras` : "Waiting for your decisions to reach ₹0"} />
        <Pair label="Time to first hold" today={hoursRange(todayMinutes)} todayNote={`over ${plural(d.worked, "ticket")}`}
          agent={d.tool.max_minutes != null ? `${d.tool.max_minutes} min` : "–"} agentNote="at most, on every ticket" />
      </div>
      <div className="table-wrap" style={{ marginBottom: 24 }}>
        <table className="table wide">
          <thead>
            <tr>
              <th>Ticket</th><th className="num">Reported</th>
              <th className="num">Victim's money held<br /><span className="note">without / with</span></th>
              <th className="num">Innocent money frozen<br /><span className="note">without / with</span></th>
              <th className="num">First hold after<br /><span className="note">without / with</span></th>
            </tr>
          </thead>
          <tbody>
            {d.tickets.map((t) => (
              <tr key={t.case_id}>
                <td><a href={`#/compare/${t.case_id}`}>{clock(t.received_at)} ticket</a>{t.after_hours ? <span className="note"> · desk closed</span> : ""}</td>
                <td className="num">{inr(t.victim_paid_inr)}</td>
                <td className="num">{inrR(t.today_victim_held_inr)} / <b>{inrR(t.tool_victim_held_inr)}</b></td>
                <td className="num">{inrR(t.today_honest_locked_inr)} / <b>{inrR(t.tool_honest_locked_now_inr)}</b></td>
                <td className="num">{duration(t.today_minutes)} / <b>{duration(t.tool_minutes)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}