import { useState } from "react";
import { api } from "../api.js";
import Decide from "../components/Decide.jsx";
import { SinceHold, byAccount } from "../components/Watch.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { evidenceBullets, evidenceLine } from "../evidence.js";
import { age, clock, hoursRange, inr, inrR, plural, when } from "../format.js";
import { useLoad } from "../hooks.js";

// The officer's Review screen (follows the console design review). Two columns: what needs the officer's approval
// (only ever the four decisions: reduce a hold, freeze a whole account, a complaint that does not match a payment,
// a release after 75 days) and what the system completed by itself. Complaints are named by the time they arrived.
const MORNING = "2026-10-18 09:00:00";
const DESK_OPENS = "2026-10-18 10:30:00";
const DAY_END = "2026-10-18 23:59:00";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (ts) => (ts ? `${Number(String(ts).slice(8, 10))} ${MONTHS[Number(String(ts).slice(5, 7)) - 1]} ${String(ts).slice(0, 4)}` : "");
const rs = (s) => String(s || "").replace(/Rs (?=\d)/g, "₹");
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const joinAnd = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);

// one group per account and decision, as the officer decides them
function groups(waiting) {
  const by = {};
  for (const w of waiting) (by[`${w.action_code}|${w.account_id}`] ||= []).push(w);
  const list = Object.values(by);
  const of = (code) => list.filter((g) => g[0].action_code === code);
  return {
    narrow: of("PROPOSE_NARROW"),
    freeze: of("PROPOSE_FULL_FREEZE").sort((a, b) => b.length - a.length || Number(b[0].amount_inr) - Number(a[0].amount_inr)),
    unmatched: of("UNMATCHED_NOTICE"),
    release: of("PROPOSE_RELEASE"),
    count: list.length,
  };
}

export default function Review({ officer }) {
  const inbox = useLoad(() => api.inbox(), []);
  const day = useLoad(() => api.day(), []);
  const clockNow = useLoad(() => api.clock(), []);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const reload = () => { inbox.reload(); day.reload(); clockNow.reload(); };

  async function run(label, fn, say) {
    setBusy(label);
    setNote(null);
    try {
      const r = await fn();
      setNote(say(r));
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
  const g = groups(data.waiting);
  const done = [...data.done].sort((a, b) => String(b.received_at).localeCompare(String(a.received_at)));
  const failed = done.filter((d) => ["FAILED", "CHECK_FAILED"].includes(d.run_status));
  const night = done.filter((d) => d.after_hours);
  const office = done.filter((d) => !d.after_hours);
  const watched = byAccount(data.watch);
  const now = clockNow.data?.clock;
  const decided = [...(data.decided || [])].sort((a, b) => String(b.decided_at).localeCompare(String(a.decided_at)));
  const narrowed = decided.filter((p) => p.action_code === "PROPOSE_NARROW");
  const frozen = decided.filter((p) => p.action_code === "PROPOSE_FULL_FREEZE");
  const otherDone = decided.filter((p) => !["PROPOSE_NARROW", "PROPOSE_FULL_FREEZE"].includes(p.action_code));

  return (
    <>
      <Band data={data} day={day} groups={g} done={done} night={night} failed={failed} now={now} />
      <main id="main" className="wrap review">
        {note && <p className="status" role="status" style={{ flex: "1 1 100%" }}>{note}</p>}

        <section className="decisions" aria-labelledby="dec-h">
          <h2 id="dec-h">Needs your approval <span className="count">{g.count + failed.length}</span></h2>
          {g.count + failed.length === 0 && (
            <p className="empty">{done.length ? `Nothing is waiting for you. ${done.length === 1 ? "The complaint is" : `All ${done.length} complaints are`} completed.` : "No complaints have been handled yet. Use Replay the night below."}</p>
          )}

          {failed.length > 0 && (
            <div className="group">
              <div className="group-h"><h3>Not completed by the system</h3><span className="count">{failed.length}</span></div>
              {failed.map((d) => <ProblemCard key={d.case_id} d={d} onDone={reload} />)}
            </div>
          )}

          {(g.narrow.length > 0 || narrowed.length > 0) && (
            <div className="group">
              <div className="group-h"><h3>{g.narrow.length > 1 ? "Genuine customers: reduce the hold" : "Genuine customer: reduce the hold"}</h3><span className="count">{g.narrow.length}</span></div>
              {g.narrow.length > 0 && <p className="group-p">The system held the full amount {g.narrow.length === 1 ? "this account" : "these accounts"} received from the fraud. Only part of it is the victim's money.</p>}
              {[...g.narrow].sort((x, y) => (Number(y[0].current_hold_inr) - Number(y[0].amount_inr)) - (Number(x[0].current_hold_inr) - Number(x[0].amount_inr)))
                .map((ps) => <GenuineCard key={ps[0].account_id} ps={ps} officer={officer} onDone={reload} watch={watched[ps[0].account_id]} />)}
              <DecidedLines list={narrowed} />
            </div>
          )}

          {(g.freeze.length > 0 || frozen.length > 0) && <MuleTable groups={g.freeze} frozen={frozen} officer={officer} onDone={reload} watched={watched} />}

          {g.unmatched.length > 0 && (
            <div className="group">
              <div className="group-h"><h3>{g.unmatched.length === 1 ? "Complaint that does not match a payment" : "Complaints that do not match a payment"}</h3><span className="count">{g.unmatched.length}</span></div>
              <p className="group-p">Nothing was held and the complaint went back to the police. A payment that looks like the one described did reach the account. If you agree it is the same payment, the system handles the complaint now.</p>
              {g.unmatched.map((ps) => <UnmatchedCard key={ps[0].proposal_id} p={ps[0]} officer={officer} onDone={reload} />)}
            </div>
          )}

          {otherDone.length > 0 && <DecidedLines list={otherDone} />}

          {g.release.length > 0 && (
            <div className="group">
              <div className="group-h"><h3>Release after 75 days on the watch</h3><span className="count">{g.release.length}</span></div>
              <p className="group-p">No police direction has confirmed these holds in 75 days.</p>
              {g.release.map((ps) => (
                <article key={ps[0].proposal_id} className="card decision">
                  <div className="decision-head"><div><h4>{ps[0].name}</h4><div className="muted">{inr(ps[0].current_hold_inr)} held · <a href={`#/case/${ps[0].case_id}`}>{ps[0].label}</a></div></div></div>
                  <Reasons p={ps[0]} />
                  <Decide p={ps[0]} officer={officer} onDone={reload} />
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="done" aria-labelledby="done-h">
          <h2 id="done-h">Completed by the system <span className="count">{done.length}</span></h2>
          <h3>After office hours · {night.length}</h3>
          {night.length ? <DoneList list={night} /> : <p className="empty">No complaints came in after office hours.</p>}
          {office.length > 0 && <OfficeHours list={office} />}
          {data.watch?.length > 0 && <Watched events={data.watch} />}
          <h3>KYC documents for police</h3>
          <div className="card pad">
            <p>{data.kyc_filed ? `${plural(data.kyc_filed, "KYC packet")} filed with the portal, in the same run as the holds. The rules allow 7 days; nothing is left to send.` : "No KYC packets were needed."}</p>
          </div>
          {data.morning_log?.text && (
            <details className="logbox">
              <summary>Morning log, as filed</summary>
              <LogText text={data.morning_log.text} />
            </details>
          )}
        </aside>

        <div className="democtl">
          <b>Demo controls</b><span>Synthetic data. Not part of the officer's screen. Simulated clock: {now ? when(now) : "not started"}.</span>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run("night", () => api.night(MORNING, true),
            (r) => `The night ran from ${clock(r.from)} to ${clock(r.until)}: ${plural(r.events, "event")} across the three banks, in ${r.seconds} s.`)}>
            {busy === "night" ? "Running the night…" : "Replay the night to 09:00"}
          </button>
          <button className="btn btn-sm" disabled={!!busy || !now || now >= DESK_OPENS} onClick={() => run("desk", () => api.night(DESK_OPENS, false),
            (r) => `Continued to ${clock(r.until)}: Bank Z's staff started at 10:00 and handled their complaint. See The whole night.`)}>
            {busy === "desk" ? "Running…" : "Continue to 10:30 (Bank Z opens)"}
          </button>
          <button className="btn btn-sm" disabled={!!busy || !now || now >= DAY_END} onClick={() => run("day", () => api.night(DAY_END, false),
            (r) => `Ran on to ${clock(r.until)}: every complaint of 18 Oct arrived and was handled (${plural(r.events, "event")}, ${r.seconds} s).`)}>
            {busy === "day" ? "Running the rest of the day (a few minutes)…" : "Run the rest of the day"}
          </button>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run("reset", () => api.reset(), () => "Reset: every complaint waits on the portal again.")}>
            {busy === "reset" ? "Resetting…" : "Reset"}
          </button>
        </div>
      </main>
    </>
  );
}

function Band({ data, day, groups: g, done, night, failed, now }) {
  const d = day.data;
  const n = done.length;
  const waitingCount = g.count + failed.length;
  let headline, sub;
  if (!n) {
    headline = "No fraud complaints handled yet.";
    sub = "New complaints are handled within 3 minutes of arriving.";
  } else if (failed.length) {
    headline = `The system completed ${n - failed.length} of ${plural(n, "complaint")}. ${plural(failed.length, "complaint")} could not be completed automatically.`;
    sub = "Please open them and finish the missing steps. They are at the top of \"Needs your approval\".";
  } else {
    const within = d?.tool?.max_minutes != null ? ` within ${plural(d.tool.max_minutes, "minute")} ${n === 1 ? "" : "each"}`.trimEnd() : "";
    headline = n === 1 ? `The system handled 1 complaint${within}.` : `The system handled all ${n} complaints${within}.`;
    headline += night.length === 0 ? " None came in after office hours." : night.length === n ? (n === 1 ? " It came in after office hours." : " All of them came in after office hours.") : ` ${night.length} came in after office hours.`;
    sub = "Money is held and reports are filed. Only the items below need your approval.";
  }
  const narrow = data.waiting.filter((w) => w.action_code === "PROPOSE_NARROW");
  const release = narrow.reduce((s, w) => s + (Number(w.current_hold_inr || 0) - Number(w.amount_inr || 0)), 0);
  const names = narrow.map((w) => String(w.name).replace(/\s*\(.*\)$/, ""));
  const nightTimes = night.map((x) => clock(x.received_at)).reverse();
  return (
    <section className="band" aria-labelledby="band-h">
      <div className="wrap">
        <p className="eyebrow">Review · {dayLabel(now || done[0]?.received_at) || "today"}</p>
        <h1 id="band-h">{headline}</h1>
        <p className="lede">{sub}</p>
        {n > 0 && (
          <div className="bandstats">
            <div className="bandstat">
              <div className="label">Genuine customers' money blocked</div>
              <div className="big">{d ? inrR(d.tool.honest_locked_now_inr) : "…"}</div>
              {d && <div className="sub">Manual process: <b>{inrR(d.today.honest_locked_inr)}</b>{d.today.accounts_frozen_whole ? `, because ${plural(d.today.accounts_frozen_whole, "full account")} ${d.today.accounts_frozen_whole === 1 ? "is" : "are"} frozen` : ""}</div>}
              {d && release > 0 && <div className="foot good">Becomes {inrR(Math.max(0, d.tool.honest_locked_now_inr - release))} after you reduce {joinAnd(names)}'s hold</div>}
            </div>
            <div className="bandstat">
              <div className="label">Victims' money secured</div>
              <div className="big">{d ? inrR(d.tool.victim_held_inr) : "…"}</div>
              {d && <div className="sub">Manual process: <b>{inrR(d.today.victim_held_inr)}</b> of {inrR(d.victim_paid_inr)} reported</div>}
            </div>
            <div className="bandstat">
              <div className="label">{night.length ? "Time to first hold, after-hours complaints" : "Time to first hold, all complaints"}</div>
              <div className="big">{d?.tool?.max_minutes != null ? `${d.tool.max_minutes} min` : "…"}</div>
              {d && <div className="sub">Manual process: <b>{hoursRange(night.length ? d.today.night_minutes : d.tickets.map((t) => t.today_minutes).filter((m) => m != null))}</b></div>}
              {nightTimes.length > 0 && <div className="foot">Complaints at {joinAnd(nightTimes)}</div>}
            </div>
            <div className={`bandstat decisions${waitingCount === 0 ? " clear" : ""}${failed.length ? " problem" : ""}`}>
              <div className="label">Needs your approval</div>
              <div className="big">{waitingCount}</div>
              <div className="sub">{waitingCount === 0 ? "Nothing waiting for you" : failed.length ? `includes ${plural(failed.length, "complaint")} the system could not complete` : `${plural(data.waiting.length, "suggestion")}, grouped by account`}</div>
            </div>
          </div>
        )}
        {n > 0 && day.error && <p className="footnote">The manual-process numbers could not be worked out: {day.error.message}</p>}
        {n > 0 && <p className="footnote">"Manual process" shows what happens today on the same complaints: staff start at 10:00, take about 20 minutes to trace the first account and 15 minutes for each next account, and freeze whole accounts.</p>}
      </div>
    </section>
  );
}

function Reasons({ p, title = "Why" }) {
  return (
    <>
      {p.reasons_for?.length > 0 && (<><h5 className="why-h">{title}</h5><ul className="why">{p.reasons_for.map((s) => <li key={s}>{rs(s)}</li>)}</ul></>)}
      {p.case_against?.length > 0 && (<><h5 className="why-h against">The case against</h5><ul className="why against">{p.case_against.map((s) => <li key={s}>{rs(s)}</li>)}</ul></>)}
    </>
  );
}

function GenuineCard({ ps, officer, onDone, watch }) {
  const p = ps[0];
  const panel = useLoad(() => api.account(p.case_id, p.account_id), [p.case_id, p.account_id]);
  const held = Number(p.current_hold_inr || 0);
  const victim = Number(p.amount_inr || 0);
  const own = Math.max(0, held - victim);
  const bal = Number(p.balance_now_inr || 0);
  const bullets = panel.data ? evidenceBullets(panel.data.signals, panel.data.inflows) : [];
  const part = (v) => `${(Math.max(v, 0) / held) * 100}%`;
  return (
    <article className="card decision">
      <div className="decision-head">
        <div>
          <h4>{p.name}</h4>
          <div className="muted">
            {p.bank_name} {p.account_type} account{p.opened_on ? `, open ${age(p.opened_on, p.proposed_at)}` : ""} · <a href={`#/case/${p.case_id}/${encodeURIComponent(p.account_id)}`}>{p.label}{p.complaint_amount_inr != null ? `, ${inr(p.complaint_amount_inr)}` : ""}</a>
          </div>
        </div>
        <span className="pill pill-honest pill-lg">Likely genuine customer</span>
      </div>
      <div className="figures">
        <div><div className="label">Balance</div><div className="val">{inr(bal)}</div></div>
        <div><div className="label">Held{p.held_since ? ` since ${clock(p.held_since)}` : ""}</div><div className="val">{inr(held)}</div></div>
        <div><div className="label">Victim's share</div><div className="val victim">{inr(victim)}</div></div>
      </div>
      {held > 0 && (
        <div className="splitbar" role="img" aria-label={`Of the ${inr(held)} held, ${inr(victim)} is the victim's and ${inr(own)} is the customer's own`}>
          {victim > 0 && <div className="victim" style={{ flex: `0 0 ${part(victim)}` }}>Victim's {inr(victim)}</div>}
          {own > 0 && <div className="own" style={{ flex: `0 0 ${part(own)}` }}>Customer's own {inr(own)}</div>}
        </div>
      )}
      <p className="split-cap">{victim > 0 ? `Of the ${inr(held)} held, ${inr(victim)} is traced to the victim.` : `None of the ${inr(held)} held is traced to the victim.`} The remaining {inr(Math.max(0, bal - held))} was never blocked.</p>
      {bullets.length > 0 && (<><h5 className="why-h">Why this looks like a genuine business</h5><ul className="why three">{bullets.map((b) => <li key={b}>{b}</li>)}</ul></>)}
      {p.citation && <p className="basis">Legal basis: {p.citation}</p>}
      <Decide p={p} officer={officer} onDone={onDone} />
      {(p.case_against?.length > 0 || watch?.length > 0) && (
        <details className="cardmore">
          <summary>The case against{watch?.length ? ", and what happened since the hold" : ""}</summary>
          {p.case_against?.length > 0 && <ul className="why against">{p.case_against.map((x) => <li key={x}>{rs(x)}</li>)}</ul>}
          <SinceHold events={watch} />
          <p className="note"><a href={`#/record/${encodeURIComponent(p.account_id)}`}>Open the hold record</a></p>
        </details>
      )}
    </article>
  );
}

function MuleTable({ groups: gs, frozen = [], officer, onDone, watched = {} }) {
  const [open, setOpen] = useState(null);
  const [more, setMore] = useState(false);
  const one = gs.length + frozen.length === 1;
  const shown = more ? gs : gs.slice(0, 10);
  return (
    <div className="group">
      <div className="group-h"><h3>{one ? "Mule account: freeze the full account?" : "Mule accounts: freeze the full account?"}</h3><span className="count">{gs.length}</span></div>
      <p className="group-p">Money passes through {one ? "this account" : "these accounts"} quickly. Open {one ? "it" : "an account"} to check the evidence before approving. One approval applies to every complaint that names the account.</p>
      <div className="table-wrap">
        <table className="table wide">
          <thead><tr><th>Account</th><th className="num">Complaints</th><th className="num">Balance to be frozen</th><th>Evidence</th><th><span className="sr-only">Action</span></th></tr></thead>
          <tbody>
            {shown.map((ps) => {
              const p = ps[0];
              const key = p.account_id;
              const bal = Number(p.amount_inr || 0);
              const tried = (watched[key] || []).some((e) => e.event_kind === "CASHOUT_BLOCKED");
              return [
                <tr key={key} className={open === key ? "open" : ""}>
                  <td className="name">{p.name}</td>
                  <td className="num">{ps.length}</td>
                  <td className="num">{inr(bal)}</td>
                  <td className="ev">{evidenceLine(p, p.proposed_at)}{tried && <div className="watchtag">Tried to withdraw held money after the hold</div>}</td>
                  <td className="act"><button className="btn btn-sm" aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>{open === key ? "Close" : bal === 0 ? "Hold money that comes in later" : "Review"}</button></td>
                </tr>,
                open === key && (
                  <tr key={key + "-panel"} className="open">
                    <td colSpan="5" className="rowpanel">
                      <Reasons p={p} title="Why a full freeze is proposed" />
                      <p className="note" style={{ margin: "8px 0" }}>Named in: {ps.map((x, i) => <span key={x.proposal_id}>{i ? ", " : ""}<a href={`#/case/${x.case_id}/${encodeURIComponent(x.account_id)}`}>{x.label}</a></span>)}. Held now, amount only: {inr(p.current_hold_inr)}.</p>
                      <SinceHold events={watched[key]} />
                      {p.citation && <p className="basis">Legal basis: {p.citation}</p>}
                      <p className="note recordlink"><a href={`#/record/${encodeURIComponent(key)}`}>Open the hold record</a></p>
                      <Decide p={p} officer={officer} onDone={() => { setOpen(null); onDone(); }} compact />
                    </td>
                  </tr>
                ),
              ];
            })}
            {frozen.map((p) => (
              <tr key={`f${p.proposal_id}`} className="donerow">
                <td className="name">{p.name}</td><td className="num">1</td><td className="num">{inr(p.amount_inr)}</td>
                <td className="ev">{evidenceLine(p, p.proposed_at)}</td>
                <td className="act">{p.proposal_status === "REJECTED" ? `Kept as it was, ${p.decided_by}, ${clock(p.decided_at)}` : `Frozen by ${p.decided_by}, ${clock(p.decided_at)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {gs.length > 10 && !more && <button className="btn btn-sm" style={{ marginTop: 8 }} onClick={() => setMore(true)}>Show {gs.length - 10} more</button>}
    </div>
  );
}

// After the officer acts: one line per decision, at the bottom of its group.
function DecidedLines({ list }) {
  if (!list.length) return null;
  const words = (p) => p.proposal_status === "REJECTED" ? "kept as it was"
    : p.action_code === "PROPOSE_NARROW" ? `hold reduced to ${inr(p.decided_amount_inr ?? p.amount_inr)}`
      : p.action_code === "UNMATCHED_NOTICE" ? "near match accepted, complaint handled" : p.action_code === "PROPOSE_RELEASE" ? "hold released" : "approved";
  return (
    <ul className="decidedlines">
      {list.map((p) => <li key={p.proposal_id}><b>{p.name.replace(/\s*\(.*\)$/, "")}</b>: {words(p)} by {p.decided_by} at {clock(p.decided_at)}</li>)}
    </ul>
  );
}

function ProblemCard({ d, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function retry() {
    setBusy(true); setError(null);
    try { await api.workOne(d.case_id); onDone(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return (
    <article className="card decision problem">
      <h4>{cap(d.label)} · {inr(d.amount_inr)}</h4>
      <p>{d.problem || "The system could not finish this complaint."}</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row">
        <a className="btn btn-sm" href={`#/case/${d.case_id}`}>Open complaint</a>
        <button className="btn btn-sm" disabled={busy} onClick={retry}>{busy ? "Trying again…" : "Try again"}</button>
      </div>
    </article>
  );
}

function UnmatchedCard({ p, officer, onDone }) {
  const n = p.reasons?.numbers || {};
  return (
    <article className="card decision">
      <div className="decision-head">
        <div>
          <h4>{cap(p.label)}: {p.name}'s account</h4>
          <div className="muted">Sent back to the police at {clock(p.proposed_at)} · <a href={`#/case/${p.case_id}`}>open the complaint</a></div>
        </div>
        <span className="pill pill-plain pill-lg">Nothing held</span>
      </div>
      <div className="figures">
        <div><div className="label">Payment that looks the same</div><div className="val">{inr(n.near_amount ?? p.amount_inr)}</div></div>
        <div><div className="label">Paid at</div><div className="val">{clock(n.near_at)}</div></div>
        <div><div className="label">Held so far</div><div className="val muted-val">Nothing yet</div></div>
      </div>
      <Reasons p={p} title="What does not match" />
      {p.citation && <p className="basis">Legal basis: {p.citation}</p>}
      <p className="note recordlink">If you accept, the system handles the complaint at once and holds the victim's money that is still there.</p>
      <Decide p={p} officer={officer} onDone={onDone} />
    </article>
  );
}

function doneText(d) {
  if (d.run_status === "SENT_BACK") return "Did not match a payment at this bank. Sent back to the police at once; nothing held.";
  if (d.run_status === "LINKED") return "The same payment as an earlier complaint: linked to it, nothing held twice.";
  if (d.run_status === "CHECK_FAILED") return "The checks did not pass, so nothing was held automatically.";
  if (d.run_status === "FAILED") return d.problem || "The system could not finish this complaint.";
  const parts = [d.holds ? `${inr(d.held_inr || 0)} held on ${plural(d.holds, "account")} by ${clock(d.sim_holds_at)}.` : "The money had already left before the complaint. Nothing could be held."];
  const extra = [d.exits && `${plural(Number(d.exits), "cash withdrawal")} reported`, d.onward && `${plural(Number(d.onward), "other bank")} informed`].filter(Boolean);
  if (extra.length) parts.push(`${cap(joinAnd(extra))}.`);
  return parts.join(" ");
}

function DoneRow({ d }) {
  return (
    <li>
      <div className="at">{clock(d.received_at)}</div>
      <div>
        <a href={`#/case/${d.case_id}`} style={{ fontWeight: 600 }}>{d.amount_inr != null ? `${inr(d.amount_inr)} reported` : cap(d.label)}</a>
        {d.ticket_kind === "ONWARD" && <span className="muted"> · {d.label.replace(/^\d\d:\d\d /, "")}</span>}
        <div className="facts">{doneText(d)}</div>
        {d.problem ? <div className="wait">Needs your attention</div>
          : d.waiting > 0 ? <div className="wait">{plural(Number(d.waiting), "item")} {Number(d.waiting) === 1 ? "needs" : "need"} your approval</div>
          : d.decided_by ? <div className="nowait">Completed by {d.decided_by}, {clock(d.decided_at)}</div>
            : <div className="nowait">Nothing waiting</div>}
      </div>
    </li>
  );
}

function DoneList({ list }) {
  const [all, setAll] = useState(false);
  const shown = all ? list : list.slice(0, 5);
  return (
    <>
      <ol className="donelist card">{shown.map((d) => <DoneRow key={d.case_id} d={d} />)}</ol>
      {list.length > 5 && !all && <button className="btn btn-sm" style={{ marginTop: 8 }} onClick={() => setAll(true)}>Show all {list.length}</button>}
    </>
  );
}

function OfficeHours({ list }) {
  const [all, setAll] = useState(false);
  const held = list.reduce((s, d) => s + Number(d.held_inr || 0), 0);
  const accounts = list.reduce((s, d) => s + Number(d.holds || 0), 0);
  return (
    <>
      <h3>During office hours · {list.length}</h3>
      {all ? <ol className="donelist card">{list.map((d) => <DoneRow key={d.case_id} d={d} />)}</ol> : (
        <div className="card pad">
          <p>{inr(held)} held on {plural(accounts, "account")}, each within 3 minutes of the complaint.</p>
          <button className="btn" onClick={() => setAll(true)}>Show all {plural(list.length, "complaint")}</button>
        </div>
      )}
    </>
  );
}

const WATCH_WORDS = {
  CASHOUT_BLOCKED: "Withdrawal refused", TRANSFER_BLOCKED: "Transfer refused", NEW_MONEY_HELD: "New money held",
  NEW_MONEY_HONEST: "Not held", LINKED_TO_VICTIM: "Linked to a victim", RECIPIENT_WATCHED: "Now watched",
  RECIPIENT_OTHER_BANK: "Reported for the police", RECIPIENT_BUSINESS: "Business account", MOVED: "Moved, not held",
  NEW_MONEY_ABOVE_NOTICE: "Above the notice", RELEASE_FLAGGED: "Release due",
};

function Watched({ events }) {
  const [all, setAll] = useState(false);
  const shown = all ? events : events.slice(0, 6);
  return (
    <>
      <h3>Watched after the holds · {events.length}</h3>
      <div className="card pad">
        <ul className="watchlist">
          {shown.map((e) => (
            <li key={e.event_id}>
              <span className="at">{clock(e.event_at)}</span>
              <span>{rs(e.sentence).replace(/^\d{2}:\d{2}: /, "")} <span className="tags"><span className="pill pill-plain">{WATCH_WORDS[e.event_kind] || e.event_kind}</span></span></span>
            </li>
          ))}
        </ul>
        {events.length > 6 && !all && <button className="btn btn-sm" onClick={() => setAll(true)}>Show all {events.length}</button>}
      </div>
    </>
  );
}

// The stored morning log: "## " lines are headings, "- " lines are points.
function LogText({ text }) {
  const out = [];
  let items = [];
  const flush = () => { if (items.length) { out.push(<ul key={`u${out.length}`} className="loglist">{items}</ul>); items = []; } };
  rs(text).split("\n").forEach((line, i) => {
    if (line.startsWith("- ")) items.push(<li key={i}>{line.slice(2)}</li>);
    else { flush(); out.push(line.startsWith("## ") ? <h5 key={i} className="why-h">{line.slice(3)}</h5> : <p key={i}>{line}</p>); }
  });
  flush();
  return <div className="morninglog">{out}</div>;
}