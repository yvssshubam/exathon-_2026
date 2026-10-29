import { useState } from "react";
import { api } from "../api.js";
import DecisionCard, { Decided, sinceLine } from "../components/DecisionCard.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { accountName, clock, duration, inrR, joinAnd, plural, rs, shortName, victim as victimName, when } from "../format.js";
import { useLoad } from "../hooks.js";

// One complaint (design board "Complaint: 02:13, Lakshmi"): where the victim's money is now, in one bar that sums
// to the paisa; the trail account by account, each card carrying what happened after the hold; the decisions on
// the right, one at a time; what the system did and what it filed.

const STEP_NAMES = { read_ticket: "Read the ticket", check_notice: "Matched the payment", find_related_cases: "Looked for earlier complaints",
  trace_money: "Traced the money", attribute_victim_share: "Worked out her share", get_account_evidence: "Read 90 days of history",
  allowed_actions: "Checked the rules", word_reasons: "Wrote the reasons", propose_action: "Proposed actions", checker: "Checker",
  write_case_summary: "Wrote the summary", error: "Stopped" };
const n = (v) => Number(v || 0);
const minutesBetween = (a, b) => Math.round((new Date(String(b).replace(" ", "T")) - new Date(String(a).replace(" ", "T"))) / 60000);

export default function Case({ caseId, officer, onChange }) {
  const detail = useLoad(() => api.caseDetail(caseId), [caseId]);
  const trail = useLoad(() => api.trail(caseId).catch(() => null), [caseId]);
  const acts = useLoad(() => api.actions(caseId).catch(() => []), [caseId]);
  const watch = useLoad(() => api.watch().catch(() => ({ events: [], watch_list: [] })), [caseId]);
  const holds = useLoad(() => api.holds().catch(() => []), [caseId]);
  const log = useLoad(() => api.log(caseId).catch(() => []), [caseId]);
  const paper = useLoad(() => api.paperwork(caseId).catch(() => []), [caseId]);
  const [pos, setPos] = useState(0);

  if (detail.loading && !detail.data) return <div className="wrap"><Loading what="Loading the complaint" /></div>;
  if (detail.error) return <div className="wrap"><Failed error={detail.error} retry={detail.reload} /></div>;
  const c = detail.data;
  const v = victimName(c.victim_tag) || "the victim";
  const waiting = (c.proposals || []).filter((p) => p.proposal_status === "WAITING");
  const decided = (c.proposals || []).filter((p) => ["APPROVED", "EDITED", "REJECTED"].includes(p.proposal_status));
  const reload = () => { detail.reload(); holds.reload(); watch.reload(); onChange?.(); };
  const events = watch.data?.events || [];
  const holdsAt = c.run?.sim_holds_at, received = c.ticket?.received_at || c.reported_at;
  const failed = ["FAILED", "CHECK_FAILED"].includes(c.run?.run_status);
  const intake = c.intake?.match_status;
  const cur = waiting[Math.min(pos, waiting.length - 1)];
  const orderNames = waiting.map((p) => shortName(p.name));

  return (
    <main id="main" className="wrap" style={{ paddingTop: 18, paddingBottom: 40 }}>
      <nav className="note" aria-label="Breadcrumb" style={{ marginBottom: 8 }}><a href="#/review">Review</a> / <a href="#/queue">Complaints</a> / {c.label}</nav>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: "12px 24px", marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 26, lineHeight: 1.2 }}>{inrR(c.amount_inr)} reported at {clock(received)} · {v}</h1>
          <p style={{ fontSize: 14, color: "#44505e", marginTop: 6 }}>
            {[c.txn_at && `Paid ${c.txn_time_known === false ? `on ${when(String(c.txn_at).slice(0, 10) + " 00:00").replace(/, 00:00$/, "")}` : clock(c.txn_at)}${c.paid_channel ? ` by ${c.paid_channel}` : ""}`, c.utr && <span key="u" className="mono">UTR {c.utr}</span>,
              [c.ordering_authority, c.ordering_reference].filter(Boolean).join(", "), c.notice_legal_basis].filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x]))}
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
            {holdsAt && <span className="pill p-night" style={{ fontSize: 13, padding: "4px 12px" }}>Held at {clock(holdsAt)}, {duration(minutesBetween(received, holdsAt))} after the complaint, {c.after_hours ? "before office hours" : "during office hours"}</span>}
            {intake === "EXACT" && <span className="pill p-honest" style={{ fontSize: 13, padding: "4px 12px" }}>Notice matches the payment</span>}
            {intake === "ACCEPTED" && <span className="pill p-wait" style={{ fontSize: 13, padding: "4px 12px" }}>Near match accepted by an officer</span>}
            {["NEAR_MATCH", "NO_MATCH", "INCOMPLETE"].includes(intake) && <span className="pill p-urgent" style={{ fontSize: 13, padding: "4px 12px" }}>Notice does not match a payment · nothing held</span>}
            {failed && <span className="pill p-urgent" style={{ fontSize: 13, padding: "4px 12px" }}>Not completed by the system</span>}
          </div>
        </div>
        {holdsAt && <a className="btn" href={`#/compare/${encodeURIComponent(caseId)}`}>Manual vs system</a>}
      </div>

      {failed && (
        <section className="card" style={{ padding: "16px 22px", marginBottom: 24, borderLeft: "6px solid #a3312a" }}>
          <p style={{ fontSize: 13, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", color: "#7a211b" }}>Not completed</p>
          <h2 style={{ fontSize: 20, marginTop: 2 }}>{rs(c.problem || "The system stopped before it finished.")}</h2>
          <p style={{ fontSize: 14, marginTop: 4 }}>Nothing was held automatically. The step that stopped is in "What the system did" below.</p>
        </section>
      )}
      {waiting.length > 0 && (
        <section style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: "14px 24px", background: "#fbf0dc", border: "1px solid #e3c58e", borderLeft: "6px solid #b07a1c", borderRadius: 12, padding: "16px 22px", marginBottom: 24 }}>
          <div style={{ flex: "1 1 320px" }}>
            <p style={{ fontSize: 13, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", color: "#8f5400" }}>Your action</p>
            <h2 style={{ fontSize: 22, lineHeight: 1.25, marginTop: 2 }}>{waiting.length === 1 ? "1 decision is waiting for you" : `${waiting.length} decisions are waiting for you`}</h2>
            <p style={{ fontSize: 14, color: "#5a3a00", marginTop: 4 }}>
              {waiting.every((p) => p.action_code === "PROPOSE_PRE_ALERT") ? "The money is already held. You decide whether to send the pre-alert. "
                : waiting.every((p) => p.action_code !== "UNMATCHED_NOTICE") ? "The money is already held. You only decide how much stays held. " : "Nothing is held yet. "}
              {waiting.length > 1 ? `Make ${waiting.length === 2 ? "both decisions" : "the decisions"} in the card on the right, one at a time. ${orderNames[0]} comes first, then ${joinAnd(orderNames.slice(1))}.` : "Decide in the card on the right."}
            </p>
          </div>
          <a className="btn btn-p" href="#decide" style={{ textDecoration: "none", alignSelf: "center" }} onClick={(e) => { e.preventDefault(); document.getElementById("decide")?.scrollIntoView({ behavior: "smooth" }); }}>Decide now ›</a>
        </section>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 32, alignItems: "flex-start" }}>
        <section style={{ flex: "1 1 600px", minWidth: 0 }} aria-labelledby="trail-h">
          {trail.data ? <Where c={c} t={trail.data} acts={acts.data || []} paper={paper.data || []} events={events} v={v} /> : null}
          {trail.data ? <Trail c={c} t={trail.data} acts={acts.data || []} events={events} watchList={watch.data?.watch_list || []} holds={holds.data || []} waiting={waiting} v={v} caseId={caseId} current={cur?.proposal_id} onPick={setPos} />
            : !trail.loading && <section className="card" style={{ padding: "18px 22px" }}><h2 style={{ fontSize: 18 }}>Nothing was traced</h2>
              <p style={{ marginTop: 6 }}>{c.intake?.mismatched ? rs(c.intake.mismatched) + ". " : ""}{intake === "NEAR_MATCH" || intake === "NO_MATCH" ? "The notice does not match a payment into the account it names, so the system sent it back to the police and held nothing." : "The system has not traced this complaint yet."}</p></section>}
        </section>
        <aside id="decide" style={{ flex: "0 1 440px", minWidth: 0, position: "sticky", top: 16 }} aria-labelledby="dec-h">
          {cur ? (
            <>
              <nav className="tabs" aria-label="Decisions on this complaint" style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14, alignItems: "center" }}>
                <span style={{ fontSize: 16, fontWeight: 700, color: "#fff", background: "#8f5400", padding: "6px 14px", borderRadius: 999, letterSpacing: "0.02em" }}>Decision {Math.min(pos, waiting.length - 1) + 1} of {waiting.length}</span>
                <span style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
                  <button className="btn btn-s" type="button" disabled={pos === 0} onClick={() => setPos((x) => Math.max(0, x - 1))}>‹ Previous</button>
                  <button className="btn btn-s" type="button" disabled={pos >= waiting.length - 1} onClick={() => setPos((x) => Math.min(waiting.length - 1, x + 1))}>Next ›</button>
                </span>
              </nav>
              <div style={{ borderTop: "6px solid #b07a1c", borderRadius: 10, boxShadow: "0 8px 24px rgba(143,84,0,0.18)" }}>
                <DecisionCard key={cur.proposal_id} p={cur} officer={officer} watch={events} variant="panel" onDone={() => { setPos(0); reload(); }} />
              </div>
              {waiting[pos + 1] && <p className="note" style={{ marginTop: 12 }}>Next: <button className="linkbtn" type="button" onClick={() => setPos(pos + 1)}>{shortName(waiting[pos + 1].name)}, {waiting[pos + 1].action_code === "PROPOSE_FULL_FREEZE" ? "freeze the account?" : waiting[pos + 1].action_code === "PROPOSE_NARROW" ? "a smaller hold?" : waiting[pos + 1].action_code === "PROPOSE_PRE_ALERT" ? "the pre-alert?" : "the near match?"}</button></p>}
            </>
          ) : decided.length ? (
            <div style={{ display: "grid", gap: 12 }}>{decided.map((p) => <Decided key={p.proposal_id} p={p} officer={officer} onDone={reload} />)}</div>
          ) : (
            <div className="card" style={{ padding: "18px 20px" }}><h2 id="dec-h" style={{ fontSize: 18 }}>Nothing is waiting for you</h2>
              <p className="note" style={{ marginTop: 4 }}>{holdsAt ? "Every hold and filing on this complaint was automatic." : "No decision on this complaint."}</p></div>
          )}
        </aside>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))", gap: 16, marginTop: 24 }}>
        {c.notice && <Notice x={c.notice} intake={c.intake} />}
        <Steps runs={log.data || []} checks={c.checks || []} />
        <Filed list={paper.data || []} holdsAt={holdsAt} v={v} />
      </div>
    </main>
  );
}

// ---------------------------------------------------------------- where her money is now
function Where({ t, acts, paper, events, v }) {
  const own = t.nodes.filter((x) => x.kind === "own");
  const heldAuto = acts.filter((a) => a.action_code === "MANDATORY_HOLD");
  const heldHers = heldAuto.reduce((s, a) => s + n(a.victim_amount_inr), 0);
  const heldAll = heldAuto.reduce((s, a) => s + n(a.amount_inr), 0);
  const ownIds = new Set(own.map((x) => x.id));
  const onward = t.edges.filter((e) => ownIds.has(e.from_account) && t.nodes.find((x) => x.id === e.to_account)?.kind === "other");
  const exits = t.edges.filter((e) => e.is_exit);
  const sum = (xs) => xs.reduce((s, e) => s + n(e.victim_inr || e.amount_inr), 0);
  const total = n(t.edges.find((e) => e.hop === 1)?.amount_inr) || heldHers + sum(onward) + sum(exits);
  const parts = [
    heldHers > 0 && ["#5b3fa0", `Held here ${inrR(heldHers)}`, heldHers],
    ...[...new Set(onward.map((e) => t.nodes.find((x) => x.id === e.to_account)?.bank_name))].map((b) => {
      const amt = sum(onward.filter((e) => t.nodes.find((x) => x.id === e.to_account)?.bank_name === b));
      return ["#3b6ea5", `Sent to ${b} ${inrR(amt)}`, amt];
    }),
    exits.length > 0 && ["#4f5966", `Cash ${inrR(sum(exits))}`, sum(exits)],
  ].filter(Boolean);
  const shown = parts.reduce((s, p) => s + p[2], 0);
  const names = (ids) => joinAnd(ids.map((id) => shortName(t.nodes.find((x) => x.id === id)?.label || id)));
  const filedAt = (kind) => clock(paper.find((p) => p.entry_kind === kind)?.filed_at);
  const refused = events.filter((e) => ownIds.has(e.account_id) && ["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"].includes(e.event_kind));
  const extra = heldAll - heldHers;
  return (
    <section className="card" style={{ padding: "22px 26px", marginBottom: 22, borderLeft: "5px solid #5b3fa0" }} aria-labelledby="where-h">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: "6px 16px", marginBottom: 10 }}>
        <h2 id="where-h" style={{ fontSize: 18 }}>Where {v === "the victim" ? "the victim's" : "her"} {inrR(total)} is now</h2>
        {Math.round(shown) === Math.round(total) && <span className="note">Adds up to {inrR(total)} to the paisa</span>}
      </div>
      <div className="bar" role="img" aria-label={parts.map((p) => p[1]).join(", ")}>
        {parts.map((p, i) => <div key={i} style={{ flex: i === parts.length - 1 ? "1 1 auto" : `0 0 ${(p[2] / (shown || 1)) * 100}%`, background: p[0] }}>{p[1]}</div>)}
      </div>
      <div style={{ marginTop: 10 }}>
        {heldHers > 0 && <div className="key"><span className="sw" style={{ background: "#5b3fa0" }} /><span><b>Held at Bank X</b> on {names(heldAuto.map((a) => a.account_id))}.{extra > 0 ? ` ${inrR(heldAll)} is held in all: ${inrR(heldHers)} is hers and ${inrR(extra)} is ${joinAnd(heldAuto.filter((a) => n(a.amount_inr) > n(a.victim_amount_inr)).map((a) => shortName(t.nodes.find((x) => x.id === a.account_id)?.label || "")).filter(Boolean).map((x) => `${x}'s`))} own, which a smaller hold would release.` : ""}</span><b>{inrR(heldHers)}</b></div>}
        {onward.map((e) => {
          const to = t.nodes.find((x) => x.id === e.to_account);
          return <div key={e.txn_id} className="key"><span className="sw" style={{ background: "#3b6ea5" }} /><span><b>Sent to {to?.bank_name}</b> from {shortName(t.nodes.find((x) => x.id === e.from_account)?.label || "")} at {clock(e.ts)}. Onward entry filed at {filedAt("ONWARD")}; {to?.bank_name} acts on it there.</span><b>{inrR(e.victim_inr || e.amount_inr)}</b></div>;
        })}
        {exits.map((e) => <div key={e.txn_id} className="key"><span className="sw" style={{ background: "#4f5966" }} /><span><b>Withdrawn as cash</b> at {e.exit_identifier || "an ATM"}, {clock(e.ts)}{e.ts < (t.edges[0]?.ts || "") ? "" : ", before the complaint"}. Reported to the police.</span><b>{inrR(e.victim_inr || e.amount_inr)}</b></div>)}
      </div>
      {refused.length > 0 && (
        <p style={{ fontSize: 14, marginTop: 10, paddingTop: 10, borderTop: "1px solid #e8ecf0" }}><b>Since the holds, {inrR(refused.reduce((s, e) => s + n(e.amount_inr), 0))} more tried to leave and was refused:</b> {joinAnd(refused.map((e) => `${shortName(e.name || e.account_id)}'s ${e.event_kind === "CASHOUT_BLOCKED" ? "ATM withdrawal" : "transfer"} at ${clock(e.event_at)}`))}.</p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- the trail, account by account
function Trail({ c, t, acts, events, watchList, holds, waiting, v, caseId, current, onPick }) {
  const byId = Object.fromEntries(t.nodes.map((x) => [x.id, x]));
  const name = (id) => shortName(byId[id]?.label || accountName(id));
  const layers = [];
  for (const x of t.nodes.filter((x) => x.kind !== "victim")) (layers[(x.hop || 1) - 1] ||= []).push({ ...x, watched: false });
  // accounts a held account tried to pay: watched, not on her trail (a dashed card beside the payer)
  const onTrail = new Set(t.nodes.map((x) => x.id));
  for (const w of watchList.filter((w) => w.case_id === caseId && !onTrail.has(w.account_id))) {
    const ev = events.find((e) => e.account_id === w.account_id && e.event_kind === "RECIPIENT_WATCHED");
    const payer = t.nodes.find((x) => ev && shortName(x.label) === shortName(ev.counterparty || ""));
    const layer = payer ? payer.hop : layers.length;
    (layers[layer] ||= []).push({ id: w.account_id, kind: "watched", label: w.name, ev, payer });
  }
  const decisionNo = Object.fromEntries(waiting.map((p, i) => [p.account_id, [i + 1, p]]));
  const auto = Object.fromEntries(acts.filter((a) => a.action_code === "MANDATORY_HOLD").map((a) => [a.account_id, a]));
  return (
    <>
      <h2 id="trail-h" style={{ fontSize: 18, marginBottom: 4 }}>The trail, account by account</h2>
      <p style={{ fontSize: 14, color: "#44505e", marginBottom: 18 }}>Read left to right. Each card shows what the account got, what the system did, and what happened after.</p>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(1, Math.min(layers.length, 4))}, minmax(0, 1fr))`, gap: 20, background: "#fff", border: "1px solid #d9dfe5", borderRadius: 12, padding: "20px 22px", overflowX: "auto" }}>
        {layers.map((col, i) => (
          <div key={i} style={{ display: "grid", gap: 12, alignContent: "start" }}>
            <p className="layer-h">Layer {i + 1}</p>
            {col.map((x) => {
              const ins = t.edges.filter((e) => e.to_account === x.id), outs = t.edges.filter((e) => e.from_account === x.id);
              const dec = decisionNo[x.id];
              const hold = auto[x.id];
              const after = events.filter((e) => e.account_id === x.id && e.case_id && e.event_kind !== "RECIPIENT_WATCHED");
              const style = { border: 0, borderLeft: "3px solid #c9d1da", borderRadius: 0, background: "transparent", boxShadow: "none", padding: "2px 0 2px 14px" };
              if (x.kind === "exit") return (
                <div key={x.id} className="hop" style={{ ...style, borderLeftColor: "#4f5966", cursor: "default" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><b>Cash out</b><span className="pill p-done">Gone</span></div>
                  <p style={{ fontSize: 13, color: "#44505e", marginTop: 4 }}>{ins.map((e) => `${inrR(e.amount_inr)} from ${name(e.from_account)} at ${x.label}, ${clock(e.ts)}.`).join(" ")} Reported to the police.</p>
                </div>);
              if (x.kind === "other") return (
                <div key={x.id} className="hop" style={{ ...style, borderLeftColor: "#3b6ea5", cursor: "default" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><b>{shortName(x.label)} · {x.bank_name}</b><span className="pill p-other">Other bank</span></div>
                  <p style={{ fontSize: 13, color: "#44505e", marginTop: 4 }}>Got {joinAnd(ins.map((e) => `${inrR(e.amount_inr)} from ${name(e.from_account)}, ${clock(e.ts)}`))}. {x.done ? `${x.bank_name} acts on it there; onward entry filed ${clock(c.run?.sim_holds_at)}.` : `${x.bank_name} handles it there.`}</p>
                </div>);
              if (x.kind === "watched") {
                const frz = holds.find((h) => h.account_id === x.id && h.hold_kind === "ACCOUNT_FREEZE" && h.hold_status === "ACTIVE");
                return (
                  <a key={x.id} className="hop" href={`#/record/${encodeURIComponent(x.id)}`} style={{ ...style, borderLeftColor: "#3b6ea5", borderLeftStyle: "dashed" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><b>{shortName(x.label)}</b><span className="pill p-plain">Watched</span></div>
                    <p style={{ fontSize: 13, color: "#44505e", marginTop: 4 }}>Not on her trail: {x.ev ? `${shortName(x.ev.counterparty || "a held account")} tried to pay ${shortName(x.label)} at ${clock(x.ev.event_at)} and the hold refused it.` : "watched after a refused payment."}{frz ? ` Frozen by ${frz.placed_by} at ${clock(frz.placed_at)} (${inrR(frz.amount_inr)}), none of it ${v}'s.` : ""}</p>
                  </a>);
              }
              const honest = x.evidence === "HONEST";
              const now = dec && dec[1].proposal_id === current;        // the decision open on the right
              const color = now ? "#8f5400" : dec ? "#b07a1c" : hold ? "#5b3fa0" : "#c9d1da";
              const hers = n(hold?.victim_amount_inr), held = n(hold?.amount_inr);
              return (
                <a key={x.id} className={`hop${now ? " now" : ""}`} href={dec ? "#decide" : `#/record/${encodeURIComponent(x.id)}`} aria-current={now ? "true" : undefined}
                  style={now ? { ...style, borderLeft: "6px solid #8f5400", borderRadius: "0 10px 10px 0", background: "#fdf3e1", boxShadow: "0 4px 14px rgba(143,84,0,0.18)", padding: "10px 12px 10px 14px" } : { ...style, borderLeftColor: color }}
                  onClick={dec ? (e) => { e.preventDefault(); onPick?.(dec[0] - 1); document.getElementById("decide")?.scrollIntoView({ behavior: "smooth", block: "nearest" }); } : undefined}>
                  {now && <span className="pill" style={{ background: "#8f5400", color: "#fff", marginBottom: 6, display: "inline-block" }}>Deciding now</span>}
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><b>{shortName(x.label)}</b>
                    {honest ? <span className="pill p-honest">Likely genuine</span> : held ? <span className="pill p-held">Held</span> : <span className="pill p-plain">Nothing left</span>}</div>
                  <p style={{ fontSize: 13, color: "#44505e", margin: "4px 0 8px" }}>
                    Got {joinAnd(ins.map((e) => `${inrR(e.amount_inr)} from ${e.from_account === t.nodes.find((z) => z.kind === "victim")?.id ? v : name(e.from_account)}, ${clock(e.ts)}`))}.
                    {outs.length > 0 && ` Sent ${joinAnd(outs.map((e) => `${inrR(e.amount_inr)} to ${byId[e.to_account]?.kind === "exit" ? "an ATM" : name(e.to_account)}`))}${outs.length && new Set(outs.map((e) => clock(e.ts))).size === 1 ? `, ${clock(outs[0].ts)}` : ""}.`}
                  </p>
                  {held > 0 && <p><b>{inrR(held)} held</b>, {hers >= held ? "all hers" : `${inrR(hers)} of it hers`}.</p>}
                  {!held && <p className="note">Nothing left to hold here.</p>}
                  {after.length > 0 && <div className="after">{after.map((e) => <span key={e.event_id}><b>{clock(e.event_at)}</b>{sinceLine(e)}</span>)}</div>}
                  {dec && <p style={{ fontSize: 13, color: "#8f5400", fontWeight: 600, marginTop: 8 }}>Decision {dec[0]}: {dec[1].action_code === "PROPOSE_NARROW" ? "smaller hold" : dec[1].action_code === "PROPOSE_FULL_FREEZE" ? "freeze the account?" : "near match"}{now ? " · open on the right" : " · click to open"}</p>}
                </a>);
            })}
          </div>
        ))}
      </div>
      <p className="note" style={{ marginTop: 14 }}>Her share in each account assumes the account spends its own money first and hers last, so an honest receiver is tied to as little as possible.</p>
    </>
  );
}

// ---------------------------------------------------------------- a police notice, as it came and as it was read
// [field, label, the intake line that says it does not match, the intake line that says it is missing]
const NOTICE_FIELDS = [["utr", "UTR", "utr:", "no utr"], ["amount_inr", "Amount", "amount:", "no amount"], ["txn_date", "Date", "date:"],
  ["txn_time", "Time", "time:"], ["beneficiary_account", "Account", "utr:", "no receiving account"], ["legal_basis", "Legal provision", null, "no legal basis"],
  ["authority", "Authority"], ["notice_ref", "Reference"], ["ack_no", "Acknowledgement", null, "acknowledgement number"], ["fir_reference", "FIR"]];

function Notice({ x, intake }) {
  const [raw, setRaw] = useState(false);
  const flagged = `${intake?.mismatched || ""}\n${intake?.problems || ""}`.toLowerCase();
  const show = (k, v) => (v == null ? null : k === "amount_inr" ? inrR(v) : k === "txn_date" ? when(v + " 00:00").replace(/, 00:00$/, "") : v);
  const matched = ["EXACT", "ACCEPTED"].includes(intake?.match_status);   // only then was every field compared
  const seen = NOTICE_FIELDS.filter(([k]) => k !== "txn_time" || x.fields?.txn_time);
  return (
    <section className="card" style={{ padding: "18px 20px", gridColumn: "1 / -1" }} aria-labelledby="notice-h">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "6px 12px", marginBottom: 8 }}>
        <h2 id="notice-h" style={{ fontSize: 17 }}>The notice as received</h2>
        <span className="note">{x.channel} from {x.sender} at {clock(x.received_at)} · read by {x.read_by === "model" ? "the local model, every value found in the text" : "the parser"}</span>
      </div>
      <table className="t"><tbody>
        {seen.map(([k, label, wrong, absent]) => {
          const v = show(k, x.fields?.[k]);
          const lines = flagged.split("\n");
          const missing = absent && lines.some((l) => l.startsWith(absent));
          const bad = !missing && wrong && lines.some((l) => l.startsWith(wrong));
          const key = wrong || absent;
          return <tr key={k}><td style={{ width: 170 }}>{label}</td>
            <td>{v == null ? <span className="note">{missing ? "Not in the notice" : k === "txn_time" ? "" : "Not given"}</span> : <span className={["utr", "beneficiary_account", "ack_no", "notice_ref"].includes(k) ? "mono" : ""}>{v}</span>}
              {k === "txn_date" && !x.fields?.txn_time && v && <span className="note"> · no time given, so the day is compared</span>}</td>
            <td style={{ width: 120, textAlign: "right" }}>{(bad || missing) ? <span className="pill p-urgent">{missing ? "Missing" : "Does not match"}</span> : matched && v != null && key ? <span className="pill p-honest">Matches</span> : null}</td></tr>;
        })}
      </tbody></table>
      {x.read_note && <p style={{ fontSize: 14, marginTop: 10, padding: "8px 12px", background: "#fbf0dc", borderLeft: "4px solid #b07a1c", borderRadius: 6 }}>{x.read_note}</p>}
      <p className="note" style={{ marginTop: 8 }}>Every field is checked against the payment in the bank's own records; nothing is held on the notice alone.{x.stmt_id ? <> <span className="stmt">stmt {x.stmt_id}</span></> : null}</p>
      <button className="linkbtn" type="button" style={{ marginTop: 8 }} onClick={() => setRaw(!raw)}>{raw ? "Hide the text" : "Show the text as received"}</button>
      {raw && <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 13, marginTop: 8, padding: "12px 14px", background: "#f5f7f9", border: "1px solid #e8ecf0", borderRadius: 8, maxHeight: 320, overflow: "auto" }}>{x.raw_text}</pre>}
    </section>
  );
}

// ---------------------------------------------------------------- what the system did, what it filed
function Steps({ runs, checks }) {
  const run = runs[0];
  if (!run) return <section className="card" style={{ padding: "18px 20px" }}><h2 style={{ fontSize: 17 }}>What the system did</h2><p className="note" style={{ marginTop: 6 }}>Not worked yet.</p></section>;
  const passed = checks.filter((k) => k.check_passed).length;
  return (
    <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="steps-h">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "6px 12px", marginBottom: 8 }}>
        <h2 id="steps-h" style={{ fontSize: 17 }}>What the system did</h2>
        <span className="note">{plural(run.steps.length, "step")} in {Number(run.wall_seconds || 0).toFixed(1)} s · {run.run_mode === "llm" ? "order chosen by the local model" : "fixed order"}{checks.length ? ` · checker passed ${passed} of ${checks.length}` : ""}</span>
      </div>
      <ol className="steps">
        {run.steps.map((s) => (
          <li key={s.step_no}><span className="note">{s.step_no}</span>
            <span><b style={{ color: s.can_change ? "#4a2f8f" : s.tool_name === "error" ? "#7a211b" : "#17212b" }}>{STEP_NAMES[s.tool_name] || s.tool_name}</b> <span style={{ color: "#44505e" }}>{rs(s.result_text)}</span></span>
            {s.stmt_ids ? <span className="stmt">stmt {String(s.stmt_ids).split(/[ ,]/)[0]}</span> : <span />}</li>
        ))}
      </ol>
      <p className="note" style={{ marginTop: 8 }}>Purple steps changed something. Only the checker can place a hold, and only after its checks pass.</p>
    </section>
  );
}

function Filed({ list, holdsAt, v }) {
  const count = (k) => list.filter((e) => e.entry_kind === k).length;
  const sum = [count("ONWARD") && plural(count("ONWARD"), "onward entry", "onward entries"), count("EXIT") && plural(count("EXIT"), "cash withdrawal"),
    count("KYC") && plural(count("KYC"), "KYC packet"), count("SEND_BACK") && "sent back to the police", count("PRE_ALERT") && plural(count("PRE_ALERT"), "pre-alert")].filter(Boolean).join(" · ");
  const pill = { ONWARD: ["Onward", "p-other"], EXIT: ["Cash", "p-done"], KYC: ["KYC", "p-plain"], SEND_BACK: ["Sent back", "p-urgent"], PRE_ALERT: ["Pre-alert", "p-wait"] };
  return (
    <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="filed-h">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "6px 12px", marginBottom: 8 }}>
        <h2 id="filed-h" style={{ fontSize: 17 }}>Filed with the portal{list[0]?.filed_at ? ` at ${clock(list[0].filed_at)}` : ""}</h2>
        <span className="note">{sum || "Nothing filed"}</span>
      </div>
      {list.length > 0 && (
        <table className="t"><tbody>
          {list.map((e) => {
            const all = n(e.victim_amount_inr) >= n(e.amount_inr) && n(e.amount_inr) > 0;
            let text;
            if (e.entry_kind === "ONWARD") text = <>{inrR(e.amount_inr)} from {shortName(e.from_account_name || accountName(e.from_account))} to {shortName(e.account_name || accountName(e.account_id))} at {e.bank_name}, {clock(e.moved_at)}. {all ? `All of it ${v === "the victim" ? "the victim's" : "hers"}.` : `${inrR(e.victim_amount_inr)} of it ${v === "the victim" ? "the victim's" : "hers"}.`} {e.utr && <span className="mono note">UTR {e.utr}</span>}</>;
            else if (e.entry_kind === "EXIT") text = `${inrR(e.amount_inr)} from ${shortName(e.from_account_name || accountName(e.from_account))} ${rs(e.detail || "").replace(/^Cash withdrawal /, "")}, ${clock(e.moved_at)}. ${all ? `All of it ${v === "the victim" ? "the victim's" : "hers"}.` : ""}`;
            else if (e.entry_kind === "KYC") {
              const f = Object.fromEntries(String(e.detail || "").split(/\s*\|\s*|\n/).map((x) => x.split(/: (.*)/s)).filter((x) => x.length > 1).map(([k, val]) => [k.trim().toLowerCase(), val.trim()]));
              const opened = f["account opened"];
              text = [f.name || shortName(e.account_name || ""), opened && `opened ${when(opened + " 00:00").replace(/, 00:00$/, "")}`, f.type].filter(Boolean).join(", ");
            }
            else if (e.entry_kind === "PRE_ALERT") text = <>{inrR(e.amount_inr)} into {shortName(e.account_name || accountName(e.account_id))} at {clock(e.moved_at)} from a new payer, who may be an unreported victim. Sent by {e.filed_by || "the officer"}. {e.utr && <span className="mono note">UTR {e.utr}</span>}</>;
            else text = rs(e.detail || "");
            return <tr key={e.entry_id}><td><span className={`pill ${pill[e.entry_kind]?.[1] || "p-plain"}`}>{pill[e.entry_kind]?.[0] || e.entry_kind}</span></td><td>{text}</td></tr>;
          })}
        </tbody></table>
      )}
      {count("KYC") > 0 && <p className="note" style={{ marginTop: 8 }}>The rules allow a week for KYC. All {count("KYC") === 1 ? "of it" : count("KYC") === 2 ? "both" : count("KYC") === 3 ? "three" : count("KYC")} went in the same run as the holds{holdsAt ? ` (${when(holdsAt)})` : ""}.</p>}
    </section>
  );
}