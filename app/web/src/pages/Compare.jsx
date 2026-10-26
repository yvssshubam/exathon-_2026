import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, duration, inrR, joinAnd, plural, shortName, victim as victimName } from "../format.js";
import { useLoad } from "../hooks.js";

// Demo view "Manual vs system" (design board "Demo: manual process vs the system"): the same complaint handled by
// today's manual desk and by the system, hour by hour on one table, then the whole day at Bank X.
const n = (v) => Number(v || 0);
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const minutesBetween = (a, b) => Math.round((new Date(String(b).replace(" ", "T")) - new Date(String(a).replace(" ", "T"))) / 60000);
const night = (ts) => { const h = Number(String(ts).slice(11, 13)); return h < 10 || h >= 18; };
const possessive = (s) => (s.endsWith("s") ? `${s}'` : `${s}'s`);

export default function Compare({ caseId }) {
  const queue = useLoad(() => api.queue(), []);
  const pick = caseId || (queue.data || []).find((t) => t.after_hours && n(t.held_inr))?.case_id || (queue.data || []).find((t) => n(t.held_inr))?.case_id;
  if (queue.loading && !queue.data) return <div className="wrap"><Loading what="Loading" /></div>;
  if (!pick) return <main id="main" className="wrap page"><h1 style={{ fontSize: 30 }}>Manual desk vs the system</h1><p className="empty">No complaint has been worked yet. Replay the night from the Review page.</p></main>;
  return <One key={pick} caseId={pick} queue={queue.data || []} />;
}

function One({ caseId, queue }) {
  const cmp = useLoad(() => api.compare(caseId, true), [caseId]);
  const detail = useLoad(() => api.caseDetail(caseId), [caseId]);
  const trail = useLoad(() => api.trail(caseId).catch(() => null), [caseId]);
  const day = useLoad(() => api.day().catch(() => null), []);
  const tag = detail.data?.victim_tag;
  const nar = useLoad(() => (tag ? api.narrator(tag).catch(() => null) : Promise.resolve(null)), [tag]);
  if ((cmp.loading && !cmp.data) || (detail.loading && !detail.data)) return <div className="wrap"><Loading what="Replaying the manual desk in Exasol" /></div>;
  if (cmp.error) return <div className="wrap"><Failed error={cmp.error} retry={cmp.reload} /></div>;
  const d = cmp.data, c = detail.data;
  const v = victimName(c.victim_tag) || "the victim";
  const her = v === "the victim" ? "The victim's" : "Her";
  const received = d.received_at;
  const dayName = DAYS[new Date(String(received).slice(0, 10)).getDay()];

  // the system side across every bank that runs it, from the narrator's view
  const banks = nar.data?.trail?.banks || [];
  const sysBanks = banks.filter((b) => b.works_tickets === "agent" && b.victim_held_inr > 0);
  const sysHeld = sysBanks.length ? sysBanks.reduce((t, b) => t + n(b.victim_held_inr), 0) : n(d.tool.victim_held_inr);
  const otherHeld = sysBanks.filter((b) => b.bank_id !== "BANK_X");
  const edges = trail.data?.edges || [];
  const exitsBefore = edges.filter((e) => e.is_exit && e.ts < received);
  const otherCash = (nar.data?.timeline || []).filter((e) => e.kind === "cash_out" && e.bank !== "BANK_X" && banks.some((b) => b.moves?.some((m) => m.from === e.account || m.to === e.account)));
  const cashNotes = [...exitsBefore.map((e) => [e.ts, n(e.victim_inr || e.amount_inr)]), ...otherCash.map((e) => [e.at, n(e.amount)])].sort();

  // the genuine customer in this complaint
  const genuine = d.today.accounts.find((a) => a.action === "WHOLE_FREEZE" && n(a.honest_locked_inr) > 0);
  const narrow = (c.proposals || []).find((p) => p.action_code === "PROPOSE_NARROW" && (!genuine || shortName(p.name) === shortName(genuine.name))) || (c.proposals || []).find((p) => p.action_code === "PROPOSE_NARROW");
  const gname = shortName(genuine?.name || narrow?.name || "");
  const sysOwn = narrow ? Math.max(0, n(narrow.current_hold_inr) - n(narrow.amount_inr)) : n(d.tool.honest_locked_now_inr);
  const inflow = edges.find((e) => trail.data?.nodes?.find((x) => x.id === e.to_account && shortName(x.label) === gname));

  // hour by hour
  const rows = [];
  const add = (ts, man, sys, both) => rows.push({ ts, man, sys, both });
  const node = (id) => shortName(trail.data?.nodes?.find((x) => x.id === id)?.label || id);
  const first = edges.find((e) => e.hop === 1);
  if (first) add(first.ts, null, null, `${v} pays ${inrR(first.amount_inr)} to ${node(first.to_account)}${c.paid_channel ? ` by ${c.paid_channel}` : ""}.`);
  for (const e of exitsBefore) add(e.ts, null, null, `${inrR(e.amount_inr)} taken as cash from ${node(e.from_account)} at ${e.exit_identifier || "an ATM"}. Gone either way.`);
  add(received, night(received) ? "Complaint arrives. Desk closed. It waits." : "Complaint arrives. The desk picks it up.", "Complaint arrives. The system starts.");
  const sysHolds = d.tool.holds_overnight.map((h) => shortName(h.name));
  add(d.tool.first_hold_at, d.today.first_hold_at <= d.tool.first_hold_at ? null : "Nothing.",
    <><span className="good">Holds {joinAnd(sysHolds)}.</span>{otherHeld.length ? ` Tells ${joinAnd(otherHeld.map((b) => b.name))}, which ${otherHeld.length === 1 ? "holds" : "hold"} ${joinAnd(otherHeld.map((b) => inrR(b.victim_held_inr)))}.` : ""}</>);
  for (const e of otherCash) add(e.at, null, null, `${inrR(e.amount)} taken as cash at ${banks.find((b) => b.bank_id === e.bank)?.name}, from the money sent on. Gone either way.`);
  for (const w of d.walked_out_after_ticket) add(w.at, <><span className="bad">{shortName(w.name)} {/cash|ATM/i.test(w.where || "") ? "withdraws" : "sends"} {inrR(w.amount_inr)}</span> {/cash|ATM/i.test(w.where || "") ? "at" : "to"} {w.where}.</>, <><span className="good">Refused.</span> {inrR(w.amount_inr)} stays in {shortName(w.name)}.</>);
  const opens = d.desk.desk_opens;
  const queued = queue.filter((t) => t.after_hours && t.received_at <= opens && t.received_at.slice(0, 10) === String(opens).slice(0, 10)).length;
  if (night(received) && opens > received) add(opens, `Desk opens. ${plural(queued || 1, "complaint")} in the queue.`, `Officer opens Review. ${plural((c.proposals || []).filter((p) => !p.proposed_at || p.proposed_at <= opens).length, "decision")} for this complaint.`);
  const byTime = {};
  for (const a of d.today.accounts) (byTime[a.action_at] ||= []).push(a);
  const late = d.today.onward_filed_at && d.tool.onward_filed_at ? minutesBetween(d.tool.onward_filed_at, d.today.onward_filed_at) : null;
  Object.entries(byTime).sort().forEach(([at, accs], i) => {
    const empty = accs.filter((a) => a.action === "NOTHING_LEFT").map((a) => shortName(a.name));
    const frozen = accs.filter((a) => a.action === "WHOLE_FREEZE");
    const liens = accs.filter((a) => a.action === "LIEN");
    const man = <>{empty.length > 0 && `Finds ${joinAnd(empty)} nearly empty. `}
      {frozen.map((a) => <span key={a.name} className={n(a.honest_locked_inr) > 0 ? "bad" : ""}>Freezes {possessive(shortName(a.name))} whole {inrR(a.balance_then_inr)}. </span>)}
      {liens.map((a) => `Holds ${inrR(a.locked_inr || a.victim_held_inr)} on ${shortName(a.name)}. `)}
      {i === 0 && late != null && late > 0 && d.today.onward_filed_at <= at.replace(/\d\d$/, "59") && otherHeld.length ? `Tells ${joinAnd(otherHeld.map((b) => b.name))}, ${duration(late).replace(" min", " min")} late.` : ""}</>;
    const sys = narrow && i === 0 ? <>Smaller hold on {shortName(narrow.name)}: {inrR(narrow.amount_inr)}. {narrow.proposal_status === "WAITING" ? <span className="pill p-wait">Waiting for your decision</span> : <span className="pill p-done">{narrow.proposal_status === "REJECTED" ? "Kept" : `Decided by ${narrow.decided_by}`}</span>}</> : <span className="note">Nothing more to do.</span>;
    add(at, man, sys);
  });
  rows.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));

  const dd = day.data;
  const pct = (a, b) => (n(b) ? Math.round((n(a) / n(b)) * 100) : 0);
  const frozenNames = (dd?.today?.frozen_names || []).map((x) => possessive(shortName(x)));
  const nightMins = dd?.today?.night_minutes || [];
  const sentBack = queue.filter((t) => t.match_status === "NEAR_MATCH" || t.match_status === "NO_MATCH");
  return (
    <main id="main" className="wrap" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <p className="note"><a href={`#/case/${encodeURIComponent(caseId)}`}>{v === "the victim" ? "The complaint" : `${v}'s complaint`}</a> · {inrR(c.amount_inr)} · reported {clock(received)}, {dayName}</p>
      <h1 style={{ fontSize: 30, lineHeight: 1.2, marginTop: 4, maxWidth: 980 }}>The same complaint, handled by the manual desk and by the system</h1>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, marginTop: 22 }}>
        <section className="card" style={{ padding: "18px 20px" }}>
          <h2 style={{ fontSize: 15 }}>{her} money held</h2>
          <div className="vs">
            <div><p className="note">Manual</p><p className="big bad">{inrR(d.today.victim_held_inr)}</p><p className="note">at Bank X.{otherHeld.length && d.today.onward_filed_at ? ` ${joinAnd(otherHeld.map((b) => b.name))} told at ${clock(d.today.onward_filed_at)}.` : ""}</p></div>
            <div><p className="note">System</p><p className="big good">{inrR(sysHeld)}</p><p className="note">{pct(sysHeld, d.victim_paid_inr)}%{sysBanks.length > 1 ? ", across both banks" : ""}</p></div>
          </div>
          {sysBanks.length > 0 && (
            <div className="bar" role="img" aria-label={`System: ${sysBanks.map((b) => `${inrR(b.victim_held_inr)} at ${b.name}`).join(" and ")}, of ${inrR(d.victim_paid_inr)}`} style={{ marginTop: 6, background: "#e8ecf0" }}>
              {sysBanks.map((b, i) => <div key={b.bank_id} style={{ flex: `0 0 ${(n(b.victim_held_inr) / n(d.victim_paid_inr)) * 100}%`, background: i ? "#3b6ea5" : "#5b3fa0" }}>{b.name} {inrR(b.victim_held_inr)}</div>)}
            </div>
          )}
          {cashNotes.length > 0 && <p className="note" style={{ marginTop: 6 }}>The rest left as cash before anyone could act: {joinAnd(cashNotes.map(([t, a]) => `${inrR(a)} at ${clock(t)}`))}.</p>}
        </section>
        <section className="card" style={{ padding: "18px 20px" }}>
          <h2 style={{ fontSize: 15 }}>{gname ? `${possessive(gname)} own money blocked` : "Genuine customers' own money blocked"}</h2>
          <div className="vs">
            <div><p className="note">Manual</p><p className="big bad">{inrR(genuine ? genuine.honest_locked_inr : d.today.honest_locked_inr)}</p><p className="note">{genuine ? "whole account frozen" : "whole accounts frozen"}</p></div>
            <div><p className="note">System</p><p className="big good">{inrR(sysOwn)}</p><p className="note">{narrow?.proposal_status === "WAITING" ? "₹0 if you approve" : "after the officer's decision"}</p></div>
          </div>
          {gname && inflow && <p className="note" style={{ marginTop: 6 }}>{gname} received {inrR(inflow.amount_inr)} from {node(inflow.from_account)}. {inrR(narrow?.amount_inr ?? inflow.victim_inr)} of it was {v === "the victim" ? "the victim's" : "hers"}.</p>}
        </section>
        <section className="card" style={{ padding: "18px 20px" }}>
          <h2 style={{ fontSize: 15 }}>First hold</h2>
          <div className="vs">
            <div><p className="note">Manual</p><p className="big bad">{clock(d.today.first_hold_at)}</p><p className="note">{duration(d.today.minutes_to_first_hold)} later</p></div>
            <div><p className="note">System</p><p className="big good">{clock(d.tool.first_hold_at)}</p><p className="note">{duration(d.tool.minutes_to_first_hold)} later</p></div>
          </div>
          <p className="note" style={{ marginTop: 6 }}>The desk is closed from 18:00 to 10:00. Most fraud money moves in that window.</p>
        </section>
      </div>

      <section className="card" style={{ marginTop: 22, overflow: "hidden" }} aria-labelledby="tl-h">
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "8px 16px", padding: "16px 18px 12px" }}>
          <h2 id="tl-h" style={{ fontSize: 18 }}>Hour by hour</h2>
          <p className="note" style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 28, height: 14, borderRadius: 3, background: "repeating-linear-gradient(135deg,#1d2a44 0 6px,#2a3a5c 6px 12px)" }} />Desk closed</p>
        </div>
        <table className="tl">
          <thead><tr><th scope="col">Time</th><th scope="col">Manual desk</th><th scope="col">The system</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={night(r.ts) ? "night" : ""}>
                <td className="time">{clock(r.ts)}</td>
                {r.both ? <td className="man" colSpan={2}>{r.both}</td> : <><td className={`man${r.man === "Nothing." ? " note" : ""}`}>{r.man}</td><td>{r.sys}</td></>}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {dd?.worked > 0 && (
        <section className="card" style={{ padding: "18px 20px", marginTop: 22 }} aria-labelledby="day-h">
          <h2 id="day-h" style={{ fontSize: 18, marginBottom: 10 }}>Across all {plural(queue.length, "complaint")} today, at Bank X</h2>
          <table className="tl" style={{ border: "1px solid #e8ecf0" }}>
            <thead><tr><th scope="col" /><th scope="col">Manual desk</th><th scope="col">The system</th></tr></thead>
            <tbody>
              <tr><td>Victims' money held, of {inrR(dd.victim_paid_inr)}</td><td>{inrR(dd.today.victim_held_inr)} ({pct(dd.today.victim_held_inr, dd.victim_paid_inr)}%)</td><td className="good">{inrR(dd.tool.victim_held_inr)} ({pct(dd.tool.victim_held_inr, dd.victim_paid_inr)}%)</td></tr>
              <tr><td>Genuine customers' money blocked</td><td className="bad">{inrR(dd.today.honest_locked_inr)}{frozenNames.length ? `, including ${joinAnd(frozenNames)} whole account${frozenNames.length === 1 ? "" : "s"}` : ""}</td>
                <td className="good">{inrR(dd.tool.honest_locked_now_inr)}{n(dd.tool.honest_locked_now_inr) ? `, ₹0 after ${plural(queue.reduce((t, x) => t + (x.waiting_code === "PROPOSE_NARROW" ? 1 : 0), 0) || 1, "approval")}` : ""}</td></tr>
              <tr><td>First hold, after-hours complaints</td><td>{nightMins.length ? (Math.min(...nightMins) === Math.max(...nightMins) ? duration(nightMins[0]) : `${duration(Math.min(...nightMins))} to ${duration(Math.max(...nightMins))}`) : "–"}</td><td className="good">{dd.tool.night_minutes?.length ? duration(Math.max(...dd.tool.night_minutes)) : duration(dd.tool.max_minutes)}</td></tr>
            </tbody>
          </table>
          <p className="note" style={{ marginTop: 8 }}>Manual desk modelled as: open {d.desk.desk_opens?.length > 5 ? clock(d.desk.desk_opens) : "10:00"} to 18:00, about {d.desk.first_lookup_minutes} min for the first account and {d.desk.per_account_minutes} min for each next, freezes whole accounts.{sentBack.length ? ` ${joinAnd(sentBack.map((t) => `${possessive(victimName(t.victim_tag))} ${clock(t.received_at)}`))} complaint${sentBack.length === 1 ? " is" : "s are"} left out of both columns because ${sentBack.length === 1 ? "its notice does" : "their notices do"} not match the payment.` : ""}</p>
        </section>
      )}
    </main>
  );
}