import { clock, plural } from "../format.js";

// What happened on watched and held accounts after the first pass, and what the agent did (pipeline/watch.py).
const KIND = {
  CASHOUT_BLOCKED: ["Withdrawal refused", "pill-held"],
  TRANSFER_BLOCKED: ["Transfer refused", "pill-held"],
  NEW_MONEY_HELD: ["New money held", "pill-held"],
  NEW_MONEY_ABOVE_NOTICE: ["Above the notice", "pill-plain"],
  NEW_MONEY_HONEST: ["Not held", "pill-honest"],
  LINKED_TO_VICTIM: ["Linked to a victim", "pill-held"],
  MOVED: ["Moved, not held", "pill-gone"],
  RECIPIENT_WATCHED: ["Recipient watched", "pill-plain"],
  RECIPIENT_BUSINESS: ["Recipient a business", "pill-plain"],
  RECIPIENT_OTHER_BANK: ["Recipient at another bank", "pill-other"],
  RELEASE_FLAGGED: ["Release due", "pill-plain"],
  PROACTIVE_HOLD: ["Held before a complaint", "pill-held"],
  NEW_MONEY_PASSED: ["Passed the checks: not held", "pill-honest"],
  LAYERING_HELD: ["Layering: for the police", "pill-plain"],
  PRE_ALERT_SENT: ["Pre-alert sent", "pill-plain"],
  PRE_ALERT_READY: ["Pre-alert waits for you", "pill-alert"],
  PRE_ALERT_ANSWERED: ["The payer complained", "pill-held"],
  PRE_ALERT_CONFIRMED: ["The payer says fraud", "pill-held"],
  PRE_ALERT_GENUINE: ["The payer says genuine", "pill-alert"],
  PRE_ALERT_NOT_NEEDED: ["Not needed", "pill-plain"],
  HOLDER_EXPLAINED: ["Holder explained", "pill-alert"],
  ESCALATED: ["With department head", "pill-alert"],
  REFER_DRAFTED: ["Referral drafted", "pill-alert"],
  MUST_DECIDE: ["Must decide", "pill-alert"],
  REFERRED: ["Referred to the police", "pill-plain"],
  POLICE_KEEP: ["Police: keep", "pill-held"],
  HOLD_RELEASED: ["Released", "pill-plain"],
  HOLD_RELEASED_LIMIT: ["Released at the limit", "pill-gone"],
};
const BLOCKED = ["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"];

// The sentence from the API, in the console's style: no leading time (shown beside it), ₹ instead of Rs.
const text = (e) => String(e.sentence || "").replace(/^\d{2}:\d{2}: /, "").replace(/Rs (?=\d)/g, "₹");

export function byAccount(events) {
  const out = {};
  for (const e of events || []) (out[e.account_id] ||= []).push(e);
  return out;
}

export function watchSummary(events) {
  const n = (kinds) => (events || []).filter((e) => kinds.includes(e.event_kind)).length;
  return [
    n(BLOCKED) && `${plural(n(BLOCKED), "attempt")} to move held money refused`,
    n(["NEW_MONEY_HELD"]) && `${plural(n(["NEW_MONEY_HELD"]), "new credit")} held under the notice`,
    n(["PROACTIVE_HOLD"]) && `${plural(n(["PROACTIVE_HOLD"]), "first-time payment")} held before any complaint`,
    n(["LINKED_TO_VICTIM"]) && `${plural(n(["LINKED_TO_VICTIM"]), "caught credit")} linked to a new victim`,
    n(["NEW_MONEY_HONEST"]) && `${plural(n(["NEW_MONEY_HONEST"]), "honest credit")} left alone`,
    n(["RECIPIENT_WATCHED"]) && `${plural(n(["RECIPIENT_WATCHED"]), "new account")} put on the watch`,
  ].filter(Boolean).join(" · ");
}

function Item({ e }) {
  const [label, pill] = KIND[e.event_kind] || [e.event_kind, "pill-plain"];
  return (
    <li>
      <span className="at">{clock(e.event_at)}</span>
      <span>
        {text(e)}
        <span className="tags">
          <span className={`pill ${pill}`}>{label}</span>
          {e.proposal_id && e.event_kind === "CASHOUT_BLOCKED" && <span className="pill pill-alert">Decision for you</span>}
        </span>
      </span>
    </li>
  );
}

// Under a decision or on an account: what this account did after the first pass.
export function SinceHold({ events, limit = 6 }) {
  if (!events?.length) return null;
  return (
    <div className="since">
      <h5>Since the first pass</h5>
      <ul className="watchlist">{events.slice(0, limit).map((e) => <Item key={e.event_id} e={e} />)}</ul>
      {events.length > limit && <p className="note">and {events.length - limit} more</p>}
    </div>
  );
}

// The morning: every watch event of the night, in time order.
export function WatchList({ events }) {
  if (!events?.length) return null;
  return (
    <div className="card pad">
      <p>{watchSummary(events)}.</p>
      <ul className="watchlist">{events.map((e) => <Item key={e.event_id} e={e} />)}</ul>
    </div>
  );
}