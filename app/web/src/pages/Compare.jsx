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
  const queue = useLoad(() => api.queue(), []);
  const handled = (queue.data || []).filter((t) => !["QUEUED", "WORKING"].includes(t.ticket_status)).length;

  if (queue.data && handled === 0) {
    return <main id="main" className="wrap page"><h1>Manual process vs the system</h1><p className="empty">No complaints received today. Replay the night from the Review page.</p></main>;
  }
  if (cmp.loading && !cmp.data) return <div className="wrap page"><Loading what="Replaying the manual process in Exasol" /></div>;
  if (cmp.error) return <div className="wrap page"><Failed error={cmp.error} retry={cmp.reload} /></div>;
  const d = cmp.data;
  const worked = !!d.tool.first_hold_at || d.tool.holds_overnight.length > 0;

  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>{tab === "day" ? "The whole day: manual process vs the system" : `The ${clock(d.received_at)} complaint: manual process vs the system`}</h1>
          <p className="lede">
            {tab === "day"
              ? "Every complaint the system has handled, replayed through the manual process on the same transactions."
              : `Same fraud, handled two ways. In the manual process, staff start at 10:00 and freeze whole accounts. With the system, money is held${d.tool.first_hold_at ? ` at ${clock(d.tool.first_hold_at)}` : ""} and only the victim's share is held.`}
          </p>
        </div>
        {handled > 1 && (
          <div className="tabs" role="tablist" aria-label="Scope">
            <button role="tab" aria-selected={tab === "ticket"} onClick={() => setTab("ticket")}>This complaint</button>
            <button role="tab" aria-selected={tab === "day"} onClick={() => setTab("day")}>Whole day, {plural(handled, "complaint")}</button>
          </div>
        )}
      </div>

      {tab === "ticket" && (
        !worked
          ? <p className="empty">The system has not handled this complaint yet. <a href={`#/case/${caseId}`}>Open the complaint</a>.</p>
          : <Ticket d={d} actions={acts.data || []} />
      )}
      {tab === "day" && <Day day={day} />}

      <p className="note">"Manual process" shows what happens today on the same {tab === "day" ? "complaints" : "complaint"}: staff start at 10:00, take about {d.desk.first_lookup_minutes} minutes to trace the first account and {d.desk.per_account_minutes} for each next one, and freeze whole accounts.{tab === "ticket" && d.today.accounts.length ? ` The victim reported at ${clock(d.received_at)} in both versions.` : ""}</p>
    </main>
  );
}

function Pair({ label, today, todayNote, agent, agentNote }) {
  const same = today === agent;
  return (
    <div className={`card pair${same ? " same" : ""}`}>
      <div className="label">{label}</div>
      <div className="two">
        <div><div className="side-h">Manual process</div><div className="v today">{today}</div><div className="n">{todayNote}</div></div>
        <div><div className="side-h agent">With the system</div><div className="v">{agent}</div><div className="n">{same ? "Same in both" : agentNote}</div></div>
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
  let honestNote = d.tool.honest_locked_overnight_inr ? "Only the customer's own money that came with the victim's" : "Nothing but the victim's money";
  if (pending.length) honestNote = `Becomes ${inrR(Math.max(0, d.tool.honest_locked_overnight_inr - release))} after you reduce ${joinAnd(pending.map((a) => `${short(a.name)}'s`))} hold`;
  else if (d.tool.honest_locked_now_inr < d.tool.honest_locked_overnight_inr) honestNote = `${inrR(d.tool.honest_locked_now_inr)} now: you reduced the hold`;

  return (
    <>
      <div className="pairs">
        <Pair label="Victim's money secured" today={inrR(d.today.victim_held_inr)}
          todayNote={emptied.length ? `${joinAnd(emptied)} emptied by ${clock(lastWalk)}` : d.today.first_hold_at ? `Held at ${clock(d.today.first_hold_at)}` : "Nothing held"}
          agent={inrR(d.tool.victim_held_inr)} agentNote={`${pct}% of the ${inr(d.victim_paid_inr)} reported`} />
        <Pair label="Genuine customers' money blocked" today={inrR(d.today.honest_locked_inr)}
          todayNote={frozenWhole.length ? `${joinAnd(frozenWhole.map((a) => short(a.name)))}'s full account${frozenWhole.length > 1 ? "s" : ""} frozen` : "No genuine customer in the trail"}
          agent={inrR(d.tool.honest_locked_overnight_inr)} agentNote={honestNote} />
        <Pair label="First hold or freeze" today={d.today.first_hold_at ? clock(d.today.first_hold_at) : "–"}
          todayNote={d.today.minutes_to_first_hold != null ? `${duration(d.today.minutes_to_first_hold)} after the complaint` : ""}
          agent={d.tool.first_hold_at ? clock(d.tool.first_hold_at) : "–"}
          agentNote={d.tool.minutes_to_first_hold != null ? `${duration(d.tool.minutes_to_first_hold)} after the complaint` : ""} />
      </div>
      {d.today.victim_held_inr === 0 && d.tool.victim_held_inr === 0 && (
        <p className="status" role="status">The money had left before the complaint came in. Neither method could stop it. The cash withdrawal is still reported to the police.</p>
      )}
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
    const transfer = /^an account at /.test(w.where);
    without.push({ at: w.at, text: crypto ? `${short(w.name)} sends ${inr(w.amount_inr)} to a crypto exchange`
      : transfer ? `${short(w.name)} sends ${inr(w.amount_inr)} to ${w.where}`
        : `${short(w.name)} withdraws ${inr(w.amount_inr)} at ${w.where.replace(/^ATM\s+\S+,\s*/, "ATM ")}` });
  }
  for (const a of d.today.accounts) {
    const text = a.action === "WHOLE_FREEZE"
      ? `${short(a.name)}'s full account frozen: ${inr(a.balance_then_inr)}, of which only ${inr(a.victim_held_inr)} is the victim's`
      : a.action === "NOTHING_LEFT" ? `Staff check ${short(a.name)} and find it empty` : `Staff hold ${inr(a.victim_held_inr)} on ${short(a.name)}`;
    without.push({ at: a.action_at, text });
  }
  if (d.today.onward_filed_at) without.push({ at: d.today.onward_filed_at, text: `Onward entry typed: ${banks} informed` });

  const agent = [];
  const holds = Object.entries(heldOn).map(([acc, amt]) => `${inr(amt)} on ${short(d.tool.holds_overnight.find((h) => h.account_id === acc).name)}`);
  if (d.tool.first_hold_at) {
    agent.push({ at: d.tool.first_hold_at, text: `${holds.length ? `Money held: ${joinAnd(holds)}.` : "Nothing left to hold; reports filed."}${d.tool.onward_filed_at ? ` ${banks} informed.` : ""}` });
  }
  for (const w of d.walked_out_after_ticket) {
    const held = heldOn[w.account_id] || 0;
    const kind = /crypto/i.test(w.where) ? "crypto transfer" : /^an account at /.test(w.where) ? "transfer" : "ATM withdrawal";
    const tries = kind === "crypto transfer" ? "to send money to a crypto exchange" : kind === "transfer" ? `to send money to ${w.where}` : "to withdraw cash at an ATM";
    agent.push({ at: w.at, text: held >= w.amount_inr ? `${short(w.name)} tries ${tries}. Blocked, because the money is held.` : held > 0 ? `${short(w.name)}'s ${kind} stopped at ${inr(w.amount_inr - held)}: ${inr(held)} is held` : `${short(w.name)}'s ${kind} goes through: nothing of the victim's was left there` });
  }
  const opens = d.desk.desk_opens;
  if (narrow.length && isNight(d.received_at)) {
    const n = narrow[0], who = short(n.name);
    const now = d.tool.holds_now.find((h) => h.account_id === n.account_id && h.kind === "LIEN");
    let text;
    if (pending.length) text = `Office opens. You reduce ${who}'s hold to ${inr(n.amount_inr)}. ${who} can use ${inr(Math.max(0, n.balance_now_inr - n.amount_inr))}`;
    else if (n.action_status === "REJECTED") text = `Office opens. You keep ${who}'s hold at ${inr(n.current_hold_inr)}`;
    else text = `Office opens. You reduce ${who}'s hold to ${inr(now ? now.amount_inr : n.amount_inr)}. ${who} can use ${inr(Math.max(0, n.balance_now_inr - (now ? now.amount_inr : n.amount_inr)))}`;
    agent.push({ at: opens, desk: true, text });
  }

  const all = [d.paid_at, d.received_at, ...without.map((e) => e.at), ...agent.map((e) => e.at)].filter(Boolean);
  // The scale fits this complaint: from 1 hour before the payment to 30 minutes after the last event
  const t0 = new Date(Math.min(...all.map(ms)) - 60 * 60000);
  t0.setMinutes(t0.getMinutes() < 30 ? 0 : 30, 0, 0);
  const t1 = new Date(Math.max(...all.map(ms)) + 30 * 60000);
  t1.setMinutes(t1.getMinutes() === 0 ? 0 : t1.getMinutes() <= 30 ? 30 : 60, 0, 0);
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
  const anyClosed = stops.some(([, closed]) => closed);
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
  const lanes = [{ name: "Manual process", marks: sorted(without, GREY) }, { name: "With the system", marks: sorted(agent, VIOLET) }];
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
          <div className="lane-track" style={{ background: anyClosed ? `linear-gradient(90deg, ${grad})` : "#F7F9FA" }}>
            {l.marks.map((m) => <span key={m.n} className="mark" title={`${clock(m.at)}: ${m.text}`} style={{ left: x(m.at), background: m.bg }}>{m.n}</span>)}
          </div>
        </div>
      ))}
      {anyClosed && (
        <div className="legend">
          <span><span className="swatch" style={{ background: "#E3E8F5" }} />Office closed</span>
          <span><span className="swatch" style={{ background: "#F7F9FA", border: "1px solid #D9DFE5" }} />Office open from 10:00</span>
        </div>
      )}
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
  if (day.loading && !day.data) return <Loading what="Replaying every complaint through the manual process" />;
  if (day.error) return <Failed error={day.error} retry={day.reload} />;
  const d = day.data;
  if (!d || !d.worked) return <p className="empty">The system hasn't handled any complaints yet. Use Replay the night on the Review page.</p>;
  const todayMinutes = d.tickets.map((t) => t.today_minutes).filter((m) => m != null);
  const pct = d.victim_paid_inr ? Math.round((d.tool.victim_held_inr / d.victim_paid_inr) * 100) : 0;
  return (
    <>
      <div className="pairs">
        <Pair label="Victims' money secured" today={inrR(d.today.victim_held_inr)} todayNote={`of ${inr(d.victim_paid_inr)} reported`}
          agent={inrR(d.tool.victim_held_inr)} agentNote={`${pct}% of the money reported`} />
        <Pair label="Genuine customers' money blocked" today={inrR(d.today.honest_locked_inr)}
          todayNote={d.today.accounts_frozen_whole ? `${plural(d.today.accounts_frozen_whole, "full account")} frozen` : "No full-account freezes"}
          agent={inrR(d.tool.honest_locked_now_inr)}
          agentNote={d.tool.honest_locked_overnight_inr > d.tool.honest_locked_now_inr ? `${inrR(d.tool.honest_locked_overnight_inr)} before you reduced the holds` : d.tool.honest_locked_now_inr ? "Becomes ₹0 after you reduce the holds waiting for you" : "No genuine customer's money blocked"} />
        <Pair label="Time to first hold" today={hoursRange(todayMinutes)} todayNote={`over ${plural(d.worked, "complaint")}`}
          agent={d.tool.max_minutes != null ? `${d.tool.max_minutes} min` : "–"} agentNote="at most, on every complaint" />
      </div>
      <div className="table-wrap" style={{ marginBottom: 24 }}>
        <table className="table wide">
          <thead>
            <tr>
              <th>Complaint</th><th className="num">Reported</th>
              <th className="num">Victim's money secured<br /><span className="note">manual / system</span></th>
              <th className="num">Genuine customers' money blocked<br /><span className="note">manual / system</span></th>
              <th className="num">First hold after<br /><span className="note">manual / system</span></th>
            </tr>
          </thead>
          <tbody>
            {d.tickets.map((t) => (
              <tr key={t.case_id}>
                <td><a href={`#/compare/${t.case_id}`}>{clock(t.received_at)} complaint</a>{t.after_hours ? <span className="note"> · after office hours</span> : ""}</td>
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