import { useState } from "react";
import { api } from "../api.js";
import Decide from "../components/Decide.jsx";
import { SinceHold, WatchList, byAccount } from "../components/Watch.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { age, clock, hoursRange, inr, inrR, plural, when } from "../format.js";
import { useLoad } from "../hooks.js";

// The officer's morning: two lists. What the agent did automatically overnight (read only) and the decisions that
// wait for an officer (only ever the four: narrow, widen to a full freeze, an unmatched notice, a release).
const ORDER = ["PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE"];
const KICKER = {
  PROPOSE_NARROW: "Narrow · an honest receiver's hold",
  PROPOSE_FULL_FREEZE: "Widen · freeze the whole account",
  UNMATCHED_NOTICE: "Unmatched notice · nothing held",
  PROPOSE_RELEASE: "Release · 75 days on the watch",
};
const MORNING = "2026-10-18 09:00:00";
const DESK_OPENS = "2026-10-18 10:30:00";

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

  if (inbox.loading && !inbox.data) return <div className="wrap"><Loading what="Loading the morning" /></div>;
  if (inbox.error) return <div className="wrap"><Failed error={inbox.error} retry={inbox.reload} /></div>;
  const data = inbox.data;
  const waiting = [...data.waiting].sort((a, b) => ORDER.indexOf(a.action_code) - ORDER.indexOf(b.action_code) || a.proposal_id - b.proposal_id);
  const done = [...data.done].sort((a, b) => String(a.received_at).localeCompare(String(b.received_at)));
  const watched = byAccount(data.watch);
  const now = clockNow.data?.clock;

  return (
    <>
      <Band data={data} day={day} now={now} done={done} waiting={waiting} />
      <main id="main" className="wrap review">
        {note && <p className="status" role="status" style={{ flex: "1 1 100%" }}>{note}</p>}
        <section className="decisions" aria-labelledby="wait-h">
          <h2 id="wait-h">Waiting for you <span className="count">{waiting.length}</span></h2>
          {waiting.length === 0 && <p className="empty">{done.length ? "Nothing is waiting for you." : "The night has not run yet. Use Replay the night below."}</p>}
          {waiting.map((p) => <DecisionCard key={p.proposal_id} p={p} officer={officer} onDone={reload} watch={watched[p.account_id]} />)}
        </section>

        <aside className="done" aria-labelledby="done-h">
          <h2 id="done-h">Done automatically overnight <span className="count">{done.length}</span></h2>
          {data.morning_log?.text && (
            <>
              <h3>The morning log</h3>
              <div className="card pad">
                <p className="morninglog">{data.morning_log.text.replace(/Rs (?=\d)/g, "₹")}</p>
                <p className="note">{data.morning_log.written_by === "model" ? "Worded by the local model from the facts below it; every number checked." : "Written from the records (fixed template)."} {when(data.morning_log.period_from)} to {when(data.morning_log.period_to)}.</p>
              </div>
            </>
          )}
          {done.length > 0 && (
            <>
              <h3>Tickets worked · {done.length}</h3>
              <ol className="donelist card">{done.map((d) => <DoneItem key={d.case_id} d={d} />)}</ol>
            </>
          )}
          {data.watch?.length > 0 && (
            <>
              <h3>On the watch since the first pass · {data.watch.length}</h3>
              <WatchList events={data.watch} />
            </>
          )}
          {data.queued > 0 && <p className="note">{plural(data.queued, "ticket")} still in the queue.</p>}
        </aside>

        <div className="democtl">
          <b>Demo controls</b><span>Synthetic data. Not part of the officer's screen. Clock: {now ? when(now) : "not started"}.</span>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run("night", () => api.night(MORNING, true),
            (r) => `The night ran from ${clock(r.from)} to ${clock(r.until)}: ${plural(r.events, "event")} across the three banks, in ${r.seconds} s.`)}>
            {busy === "night" ? "Running the night…" : "Replay the night to 09:00"}
          </button>
          <button className="btn btn-sm" disabled={!!busy || !now || now >= DESK_OPENS} onClick={() => run("desk", () => api.night(DESK_OPENS, false),
            (r) => `Continued to ${clock(r.until)}: Bank Z's desk opened at 10:00 and worked its ticket. See The whole night.`)}>
            {busy === "desk" ? "Running…" : "Continue to 10:30 (Bank Z's desk opens)"}
          </button>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run("reset", () => api.reset(), () => "Reset: every ticket waits on the portal again.")}>
            {busy === "reset" ? "Resetting…" : "Reset"}
          </button>
        </div>
      </main>
    </>
  );
}

function Band({ data, day, now, done, waiting }) {
  const d = day.data;
  const night = done.filter((x) => x.after_hours);
  const headline = done.length
    ? `The agent worked ${plural(done.length, "ticket")} overnight. ${waiting.length ? `${plural(waiting.length, "decision")} ${waiting.length === 1 ? "waits" : "wait"} for you.` : "Nothing waits for you."}`
    : "No tickets worked yet.";
  return (
    <section className="band" aria-labelledby="band-h">
      <div className="wrap">
        <p className="eyebrow">This morning · {now ? when(now) : "before the night"} (simulated clock)</p>
        <h1 id="band-h">{headline}</h1>
        <p className="lede">{done.length ? "Mandatory holds, filings, KYC packets and send-backs are done and listed under Done automatically overnight. Only the decisions below need an officer." : "Replay the night to watch three banks work the same fraud, each seeing only its own data."}</p>
        {done.length > 0 && d && (
          <div className="bandstats">
            <div className="bandstat">
              <div className="label">Innocent customers' money held</div>
              <div className="big">{inrR(d.tool.honest_locked_now_inr)}</div>
              <div className="sub">Without the agent: <b>{inrR(d.today.honest_locked_inr)}</b>{d.today.accounts_frozen_whole ? `, ${plural(d.today.accounts_frozen_whole, "account")} frozen in full` : ""}</div>
            </div>
            <div className="bandstat">
              <div className="label">Victims' money held back</div>
              <div className="big">{inrR(d.tool.victim_held_inr)}</div>
              <div className="sub">Without the agent: <b>{inrR(d.today.victim_held_inr)}</b> of {inrR(d.victim_paid_inr)} reported</div>
            </div>
            <div className="bandstat">
              <div className="label">Time to first hold</div>
              <div className="big">{d.tool.max_minutes != null ? plural(d.tool.max_minutes, "min", "min") : "–"}</div>
              <div className="sub">Without the agent: <b>{hoursRange(night.length ? d.today.night_minutes : d.tickets.map((t) => t.today_minutes).filter((m) => m != null))}</b></div>
              {night.length > 0 && <div className="foot">{plural(night.length, "ticket")} landed while the desk was closed</div>}
            </div>
            <div className="bandstat decisions">
              <div className="label">Waiting for you</div>
              <div className="big">{waiting.length}</div>
              <div className="sub">of {plural(data.done.length, "ticket")} worked</div>
            </div>
          </div>
        )}
        {done.length > 0 && day.error && <p className="footnote">The before/after numbers could not be worked out: {day.error.message}</p>}
        {done.length > 0 && <p className="footnote">"Without the agent" is the bank's manual process today, replayed in Exasol on the same transactions: the desk opens at 10:00, about 20 minutes for the first account and 15 for each next one, whole accounts frozen.</p>}
      </div>
    </section>
  );
}

function Figures({ p }) {
  const n = p.reasons?.numbers || {};
  if (p.action_code === "PROPOSE_NARROW") {
    const held = Number(p.current_hold_inr ?? n.held ?? 0);
    const victims = Number(p.amount_inr || 0);
    const own = Math.max(0, held - victims);
    return (
      <>
        <div className="figures">
          <div><div className="label">Held now</div><div className="val">{inr(held)}</div></div>
          <div><div className="label">Victims' money in it</div><div className="val victim">{inr(victims)}</div></div>
          <div><div className="label">Balance</div><div className="val">{inr(p.balance_now_inr)}</div></div>
        </div>
        {held > 0 && (
          <div className="splitbar" role="img" aria-label={`Of the ${inr(held)} held, ${inr(victims)} is victims' money and ${inr(own)} is the holder's own`}>
            {victims > 0 && <div className="victim" style={{ flex: `0 0 ${(victims / held) * 100}%` }}>Victims' {inr(victims)}</div>}
            {own > 0 && <div className="own" style={{ flex: `0 0 ${(own / held) * 100}%` }}>Their own {inr(own)}</div>}
          </div>
        )}
      </>
    );
  }
  if (p.action_code === "PROPOSE_FULL_FREEZE") {
    return (
      <div className="figures">
        <div><div className="label">Balance that would be frozen</div><div className="val">{inr(p.amount_inr)}</div></div>
        <div><div className="label">Held amount-only now</div><div className="val">{inr(n.held ?? p.current_hold_inr)}</div></div>
        <div><div className="label">Account age</div><div className="val">{age(p.opened_on, p.proposed_at) || "–"}</div></div>
      </div>
    );
  }
  if (p.action_code === "UNMATCHED_NOTICE") {
    return (
      <div className="figures">
        <div><div className="label">Near match</div><div className="val">{inr(n.near_amount ?? p.amount_inr)}</div></div>
        <div><div className="label">Paid at</div><div className="val">{clock(n.near_at)}</div></div>
        <div><div className="label">Held</div><div className="val">₹0</div></div>
      </div>
    );
  }
  return null;
}

function DecisionCard({ p, officer, onDone, watch }) {
  const by = p.proposed_by === "watch" ? "the watch" : p.proposed_by === "llm" ? "the agent (model-led)" : "the agent";
  return (
    <article className="card decision">
      <div className="kicker">{KICKER[p.action_code] || p.action_code}</div>
      <div className="decision-head">
        <div>
          <h4>{String(p.headline || p.name).replace(/Rs (?=\d)/g, "₹")}</h4>
          <div className="muted">
            {p.name} · <a href={`#/case/${p.case_id}/${encodeURIComponent(p.account_id)}`}>{p.case_id}</a> · proposed {clock(p.proposed_at)} by {by}
          </div>
        </div>
      </div>
      <Figures p={p} />
      <div style={{ height: 12 }} />
      {p.reasons_for?.length > 0 && (
        <>
          <h5 className="why-h">Why</h5>
          <ul className="why">{p.reasons_for.map((s) => <li key={s}>{s.replace(/Rs (?=\d)/g, "₹")}</li>)}</ul>
        </>
      )}
      {p.case_against?.length > 0 && (
        <>
          <h5 className="why-h against">The case against</h5>
          <ul className="why against">{p.case_against.map((s) => <li key={s}>{s.replace(/Rs (?=\d)/g, "₹")}</li>)}</ul>
        </>
      )}
      <SinceHold events={watch} />
      {p.citation && <p className="basis">Legal basis: {p.citation}</p>}
      {p.claims_check && <p className="note">The model's wording was not used: {p.claims_check}. The fixed template is shown.</p>}
      <p className="note"><a href={`#/record/${encodeURIComponent(p.account_id)}`}>Open the hold record</a>: the notice, the path, the lowest-balance steps and every statement ID.</p>
      <Decide p={p} officer={officer} onDone={onDone} />
    </article>
  );
}

function facts(d) {
  if (d.run_status === "SENT_BACK") return "Did not match a payment: sent back to the police at once. Nothing held.";
  if (d.run_status === "LINKED") return "The same payment as an earlier case: linked. No second hold.";
  if (d.run_status === "CHECK_FAILED") return "The checker stopped the automatic holds. Needs you.";
  if (d.run_status === "FAILED") return "The agent's run failed; see the ticket's log.";
  const parts = [d.holds ? `Held ${inr(d.held_inr || 0)} on ${plural(d.holds, "account")} by ${clock(d.sim_holds_at)}.` : "Nothing left to hold."];
  if (d.filed) parts.push(`${plural(d.filed, "filing")} sent to the portal in the same run.`);
  return parts.join(" ");
}

function DoneItem({ d }) {
  return (
    <li>
      <div className="at">{clock(d.received_at)}</div>
      <div>
        <a href={`#/case/${d.case_id}`} style={{ fontWeight: 600 }}>{d.case_id}</a>{d.amount_inr != null && <> · {inr(d.amount_inr)} reported</>}
        {d.ticket_kind === "ONWARD" && <span className="muted"> · onward entry from another bank</span>}
        <div className="facts">{facts(d)}</div>
        {d.waiting > 0 && <div className="wait">{plural(d.waiting, "decision")} for you</div>}
      </div>
    </li>
  );
}