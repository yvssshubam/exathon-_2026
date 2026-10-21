import { useState } from "react";
import { api } from "../api.js";
import Decide from "../components/Decide.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { evidenceBullets, evidenceLine } from "../evidence.js";
import { age, clock, hoursRange, inr, inrR, plural } from "../format.js";
import { useLoad } from "../hooks.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (ts) => (ts ? `${Number(ts.slice(8, 10))} ${MONTHS[Number(ts.slice(5, 7)) - 1]} ${ts.slice(0, 4)}` : "");
const DECIDED = ["APPROVED", "EDITED", "REJECTED"];

// Proposals grouped the way the officer decides them: one card per honest customer, one row per mule account
// (however many tickets name it).
function groupDecisions(waiting) {
  const groups = {};
  for (const w of waiting) {
    const key = `${w.action_code}|${w.account_id}`;
    (groups[key] ||= []).push(w);
  }
  const list = Object.values(groups);
  return {
    narrow: list.filter((g) => g[0].action_code === "PROPOSE_NARROW"),
    freeze: list.filter((g) => g[0].action_code === "PROPOSE_FULL_FREEZE")
      .sort((a, b) => b.length - a.length || Math.max(...b.map((p) => p.balance_now_inr)) - Math.max(...a.map((p) => p.balance_now_inr))),
    confirm: list.filter((g) => g[0].action_code === "CONFIRM_HOLD"),
    count: list.length,
  };
}

export default function Review({ officer }) {
  const inbox = useLoad(() => api.inbox(), []);
  const day = useLoad(() => api.day(), []);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const reload = () => { inbox.reload(); day.reload(); };

  async function run(label, fn) {
    setBusy(label);
    setNote(null);
    try {
      const r = await fn();
      if (r?.worked) setNote(`The agent worked ${plural(r.worked.length, "ticket")}.`);
      reload();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (inbox.loading && !inbox.data) return <div className="wrap"><Loading what="Loading the review" /></div>;
  if (inbox.error) return <div className="wrap"><Failed error={inbox.error} retry={inbox.reload} /></div>;
  const data = inbox.data;
  const g = groupDecisions(data.waiting);
  const night = data.done.filter((d) => d.after_hours).sort((a, b) => a.received_at.localeCompare(b.received_at));
  const dayTickets = data.done.filter((d) => !d.after_hours).sort((a, b) => a.received_at.localeCompare(b.received_at));

  return (
    <>
      <Band data={data} day={day} groups={g} night={night} />
      <main id="main" className="wrap review">
        {note && <p className="status" role="status" style={{ flex: "1 1 100%" }}>{note}</p>}
        <section className="decisions" aria-labelledby="dec-h">
          <h2 id="dec-h">Decisions for you <span className="count">{g.count}</span></h2>
          {g.count === 0 && <p className="empty">{data.done.length ? "Nothing is waiting for you." : "The agent hasn't worked any tickets yet. Use Replay the day below."}</p>}

          {g.narrow.length > 0 && (
            <div className="group">
              <div className="group-h"><h3>Honest customer: release part of the hold</h3><span className="count">{g.narrow.length}</span></div>
              <p className="group-p">The agent held everything {g.narrow.length === 1 ? "this account" : "these accounts"} got from the fraud. Only part of it is the victim's money.</p>
              {g.narrow.map((ps) => <HonestCard key={ps[0].account_id} proposals={ps} officer={officer} onDone={reload} />)}
            </div>
          )}

          {g.freeze.length > 0 && <MuleTable groups={g.freeze} officer={officer} onDone={reload} />}

          {g.confirm.length > 0 && (
            <div className="group">
              <div className="group-h"><h3>Borderline account: confirm the hold</h3><span className="count">{g.confirm.length}</span></div>
              <p className="group-p">The account's history is mixed. The amount-only hold stays until you confirm or drop it.</p>
              {g.confirm.map((ps) => (
                <article key={ps[0].account_id} className="card decision">
                  <div className="decision-head"><div><h4>{ps[0].name}</h4><div className="muted">{inr(ps[0].amount_inr)} held, <a href={`#/case/${ps[0].case_id}/${encodeURIComponent(ps[0].account_id)}`}>{ps[0].case_id}</a></div></div></div>
                  <Decide proposals={ps} officer={officer} onDone={reload} />
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="done" aria-labelledby="done-h">
          <h2 id="done-h">Already done by the agent <span className="count">{data.done.length}</span></h2>
          {night.length > 0 && (
            <>
              <h3>While the desk was closed · {night.length}</h3>
              <ol className="donelist card">
                {night.map((d) => <DoneItem key={d.case_id} d={d} />)}
              </ol>
            </>
          )}
          {dayTickets.length > 0 && <DayTickets list={dayTickets} />}
          {data.kyc_to_send.length > 0 && <Paperwork list={data.kyc_to_send} officer={officer} onDone={reload} />}
          {data.done.length === 0 && <p className="empty">Nothing yet.</p>}
        </aside>

        <div className="democtl">
          <b>Demo controls</b><span>Synthetic data. Not part of the officer's screen.</span>
          <button className="btn btn-sm" disabled={!!busy || data.queued === 0} onClick={() => run("replay", () => api.workQueue())}>
            {busy === "replay" ? "The agent is working the tickets…" : data.queued ? `Replay the day (${plural(data.queued, "ticket")} waiting)` : "Replay the day"}
          </button>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run("reset", () => api.reset())}>{busy === "reset" ? "Resetting…" : "Reset"}</button>
        </div>
      </main>
    </>
  );
}

function Band({ data, day, groups, night }) {
  const worked = data.done.length;
  const total = worked + data.queued;
  const d = day.data;
  const at = data.done[0]?.received_at;
  let headline;
  if (!worked) headline = `${plural(total, "ticket")} waiting for the agent.`;
  else {
    const most = d?.tool?.max_minutes != null ? ` within ${plural(d.tool.max_minutes, "minute")} each` : "";
    headline = `The agent handled ${worked === total ? `all ${worked} tickets` : `${worked} of ${total} tickets`}${most}. ${night.length ? `${night.length} came in while the desk was closed.` : ""}`;
  }
  const pendingRelease = data.waiting.filter((w) => w.action_code === "PROPOSE_NARROW").reduce((s, w) => s + (w.current_hold_inr - w.amount_inr), 0);
  const biggest = d?.tickets?.slice().sort((a, b) => (b.tool_victim_held_inr - b.today_victim_held_inr) - (a.tool_victim_held_inr - a.today_victim_held_inr))[0];
  const maxTickets = Math.max(0, ...groups.freeze.map((g2) => g2.length));
  return (
    <section className="band" aria-labelledby="band-h">
      <div className="wrap">
        <p className="eyebrow">Review · {dayLabel(at) || "today"}</p>
        <h1 id="band-h">{headline}</h1>
        <p className="lede">{worked ? "Required holds are done and paperwork is filed. Only the decisions below need you." : "Replay the day to watch the agent work every ticket the moment it lands."}</p>
        {worked > 0 && (
          <div className="bandstats">
            <div className="bandstat">
              <div className="label">Innocent customers' money frozen</div>
              <div className="big">{d ? inrR(d.tool.honest_locked_now_inr) : "…"}</div>
              {d && <div className="sub">Without the agent: <b>{inrR(d.today.honest_locked_inr)}</b>{d.today.accounts_frozen_whole ? `, ${plural(d.today.accounts_frozen_whole, "account")} frozen in full` : ""}</div>}
              {d && pendingRelease > 0 && <div className="foot good">{inrR(Math.max(0, d.tool.honest_locked_now_inr - pendingRelease))} once you release the extra {inrR(pendingRelease)}</div>}
            </div>
            <div className="bandstat">
              <div className="label">Victims' money held back</div>
              <div className="big">{d ? inrR(d.tool.victim_held_inr) : "…"}</div>
              {d && <div className="sub">Without the agent: <b>{inrR(d.today.victim_held_inr)}</b> of {inrR(d.victim_paid_inr)} reported</div>}
              {d && biggest && biggest.tool_victim_held_inr > biggest.today_victim_held_inr && d.tickets.length > 1 && (
                <div className="foot">{inrR(biggest.tool_victim_held_inr - biggest.today_victim_held_inr)} of the difference is the {clock(biggest.received_at)} ticket</div>
              )}
            </div>
            <div className="bandstat">
              <div className="label">{night.length ? "Time to first hold, night tickets" : "Time to first hold"}</div>
              <div className="big">{d ? (d.tool.max_minutes != null ? plural(d.tool.max_minutes, "min", "min") : "–") : "…"}</div>
              {d && <div className="sub">Without the agent: <b>{hoursRange(night.length ? d.today.night_minutes : d.tickets.map((t) => t.today_minutes).filter((m) => m != null))}</b></div>}
              {night.length > 0 && <div className="foot">Ticket{night.length === 1 ? "" : "s"} at {night.map((n) => clock(n.received_at)).join(", ")}</div>}
            </div>
            <div className="bandstat decisions">
              <div className="label">Decisions for you</div>
              <div className="big">{groups.count}</div>
              <div className="sub">{plural(data.waiting.length, "suggestion")}, grouped by account</div>
              {maxTickets > 1 && <div className="foot">An account named in {maxTickets} tickets is still 1 decision</div>}
            </div>
          </div>
        )}
        {worked > 0 && day.error && <p className="footnote">The before/after numbers could not be worked out: {day.error.message}</p>}
        {worked > 0 && !d && !day.error && <p className="footnote">Replaying today's manual process in Exasol for the before/after numbers…</p>}
        {worked > 0 && <p className="footnote">"Without the agent" is the bank's manual process today, replayed on the same tickets: staff start at 10:00, take about 20 minutes to trace the first account and 15 for each next one, and freeze whole accounts.</p>}
      </div>
    </section>
  );
}

function HonestCard({ proposals, officer, onDone }) {
  const p = proposals[0];
  const panel = useLoad(() => api.account(p.case_id, p.account_id), [p.case_id, p.account_id]);
  const victim = p.victim_amount_inr, others = p.other_victims_inr, held = p.current_hold_inr;
  const own = Math.max(0, held - victim - others);
  const pn = panel.data;
  const bullets = pn ? evidenceBullets(pn.signals, pn.inflows) : [];
  const part = (v) => `${Math.max(v, 0) / held * 100}%`;
  return (
    <article className="card decision">
      <div className="decision-head">
        <div>
          <h4>{p.name}</h4>
          <div className="muted">
            {p.bank_name} {p.account_type} account{p.opened_on ? `, open ${age(p.opened_on, p.received_at)}` : ""} · <a href={`#/case/${p.case_id}/${encodeURIComponent(p.account_id)}`}>{clock(p.received_at)} ticket</a>
          </div>
        </div>
        <span className="pill pill-honest pill-lg">{p.reason_code === "NODAL_POOL_ESCROW" ? "Nodal, pool or escrow account" : "Likely honest receiver"}</span>
      </div>
      <div className="figures">
        <div><div className="label">Balance</div><div className="val">{inr(p.balance_now_inr)}</div></div>
        <div><div className="label">Held{p.held_since ? ` since ${clock(p.held_since)}` : ""}</div><div className="val">{inr(held)}</div></div>
        <div><div className="label">Victim's money in it</div><div className="val victim">{inr(victim + others)}</div></div>
      </div>
      <div className="splitbar" role="img" aria-label={`Of the ${inr(held)} held, ${inr(victim + others)} is victims' money and ${inr(own)} is the holder's own`}>
        {victim > 0 && <div className="victim" style={{ flex: `0 0 ${part(victim)}` }}>Victim's {inr(victim)}</div>}
        {others > 0 && <div className="others" style={{ flex: `0 0 ${part(others)}` }}>Other victims' {inr(others)}</div>}
        {own > 0 && <div className="own" style={{ flex: `0 0 ${part(own)}` }}>Their own {inr(own)}</div>}
      </div>
      <p className="split-cap">Of the {inr(held)} held, {inr(victim + others)} traces back to the fraud. The other {inr(p.balance_now_inr - held)} was never held.</p>
      {bullets.length > 0 && (
        <>
          <h5 className="why-h">Why it looks like a real business</h5>
          <ul className="why">{bullets.map((b) => <li key={b}>{b}</li>)}</ul>
        </>
      )}
      <p className="basis">Legal basis: hold only the victim's amount (SOP 5(xii), 9.1(i); Delhi HC, Neelkanth Pharma). One fraud-linked payment does not make the holder a suspect (AP HC, 22 Jun 2026).</p>
      <Decide proposals={proposals} officer={officer} onDone={onDone} />
    </article>
  );
}

function MuleTable({ groups, officer, onDone }) {
  const [open, setOpen] = useState(null);
  const empty = groups.filter((g) => Number(g[0].balance_now_inr) === 0);
  return (
    <div className="group">
      <div className="group-h"><h3>Mule accounts: freeze the whole account?</h3><span className="count">{groups.length}</span></div>
      <p className="group-p">These accounts pass money straight through. Open one to check the evidence. One approval covers every ticket that names it.</p>
      <div className="table-wrap">
        <table className="table wide">
          <thead>
            <tr><th>Account</th><th className="num">Tickets</th><th className="num">Balance that would be frozen</th><th>Evidence</th><th><span className="sr-only">Action</span></th></tr>
          </thead>
          <tbody>
            {groups.map((ps) => {
              const p = ps[0];
              const key = p.account_id;
              const bal = Math.max(...ps.map((x) => x.balance_now_inr));
              return [
                <tr key={key} className={open === key ? "open" : ""}>
                  <td className="name">{p.name}</td>
                  <td className="num">{ps.length}</td>
                  <td className="num">{inr(bal)}</td>
                  <td className="ev">{evidenceLine(p)}</td>
                  <td className="act"><button className="btn btn-sm" aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>{open === key ? "Close" : bal === 0 ? "Watch for new money" : "Review"}</button></td>
                </tr>,
                open === key && (
                  <tr key={key + "-panel"} className="open">
                    <td colSpan="5" className="rowpanel">
                      <ul className="reasons" style={{ paddingLeft: 18, margin: "4px 0 10px", fontSize: 15 }}>{p.explanation.map((s) => <li key={s}>{s}</li>)}</ul>
                      <p className="note" style={{ marginBottom: 10 }}>
                        Named in: {ps.map((x, i) => <span key={x.case_id}>{i ? ", " : ""}<a href={`#/case/${x.case_id}/${encodeURIComponent(x.account_id)}`}>{clock(x.received_at)} ticket ({x.case_id})</a></span>)}. Already held: {inr(ps.reduce((s, x) => s + (x.current_hold_inr || 0), 0))}.
                      </p>
                      <Decide proposals={ps} officer={officer} onDone={() => { setOpen(null); onDone(); }} compact />
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
      {empty.length > 0 && <p className="note" style={{ marginTop: 8 }}>{empty.map((g2) => g2[0].name).join(", ")} {empty.length === 1 ? "is" : "are"} already empty, so there is nothing to freeze. Approving holds any new money that arrives.</p>}
    </div>
  );
}

function facts(d) {
  if (d.run_status === "SENT_BACK") return "Notice sent back to the police for correction. Nothing frozen.";
  if (d.run_status === "CHECK_FAILED") return "The checker stopped the automatic holds. Needs you.";
  if (d.run_status === "FAILED") return "The agent's run failed; see the ticket's log.";
  const parts = [`Held ${inr(d.held_inr || 0)} on ${plural(d.holds, "account")} by ${clock(d.sim_holds_at)}.`];
  if (d.exits) parts.push(`${plural(d.exits, "cash-out")} reported`);
  if (d.onward) parts.push(`${plural(d.onward, "other bank")} alerted`);
  return parts.length > 1 ? `${parts[0]} ${parts.slice(1).join(", ")}.` : parts[0];
}

function DoneItem({ d }) {
  return (
    <li>
      <div className="at">{clock(d.received_at)}</div>
      <div>
        <a href={`#/case/${d.case_id}`} style={{ fontWeight: 600 }}>{inr(d.amount_inr)} reported</a>
        <div className="facts">{facts(d)}</div>
        {d.waiting > 0 && <div className="wait">{plural(d.waiting, "decision")} for you</div>}
      </div>
    </li>
  );
}

function DayTickets({ list }) {
  const [all, setAll] = useState(false);
  const held = list.reduce((s, d) => s + (d.held_inr || 0), 0);
  const accounts = list.reduce((s, d) => s + (d.holds || 0), 0);
  const exits = list.reduce((s, d) => s + (d.exits || 0), 0);
  const onward = list.reduce((s, d) => s + (d.onward || 0), 0);
  return (
    <>
      <h3>During office hours · {list.length}</h3>
      {all ? (
        <ol className="donelist card">{list.map((d) => <DoneItem key={d.case_id} d={d} />)}</ol>
      ) : (
        <div className="card pad">
          <p>Held {inr(held)} on {plural(accounts, "account")}, each within 3 minutes of its ticket. {plural(exits, "cash-out")} reported to police, {plural(onward, "alert")} sent to other banks.</p>
          <button className="btn" onClick={() => setAll(true)}>Show all {list.length} tickets</button>
        </div>
      )}
    </>
  );
}

function Paperwork({ list, officer, onDone }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  async function send(items) {
    if (!officer.trim()) return setError("Type your name in the Officer box at the top first.");
    setBusy(true);
    setError(null);
    try {
      for (const k of items) await api.sendKyc(k.case_id, k.entry_id, { officer });
      onDone();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h3>Paperwork to send</h3>
      <div className="card pad">
        <p>{plural(list.length, "KYC packet")} drafted. Due within a week of each ticket.</p>
        {open && (
          <ul className="kyclist">
            {list.map((k) => (
              <li key={k.entry_id}><span>{k.name} <span className="muted">· <a href={`#/case/${k.case_id}`}>{k.case_id}</a></span></span>
                <button className="btn btn-sm" disabled={busy} onClick={() => send([k])}>Send</button></li>
            ))}
          </ul>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="row">
          {open ? <button className="btn" disabled={busy} onClick={() => send(list)}>{busy ? "Sending…" : `Send all ${list.length}`}</button>
                : <button className="btn" onClick={() => setOpen(true)}>Review and send</button>}
        </div>
      </div>
    </>
  );
}