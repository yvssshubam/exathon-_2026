import { clock, inr, plural } from "../format.js";

// What the agent saw and did on held accounts after the holds went on (pipeline/watch.py).
const KIND = { BLOCKED: "Refused", MOVED: "Moved", NEW_MONEY: "New money", MARKER_HOLD: "Caught" };
// The sentence from the API, in the console's style: no leading time (shown beside it), ₹ instead of Rs, and
// without "a decision is waiting" when it already sits under that decision.
const text = (e, under) => {
  let t = String(e.sentence || "").replace(/^\d{2}:\d{2}: /, "").replace(/Rs (?=\d)/g, "₹");
  if (under) t = t.replace(/ A decision on .* is waiting for you\.$/, "");
  return t;
};

export function byAccount(events) {
  const out = {};
  for (const e of events || []) (out[e.account_id] ||= []).push(e);
  return out;
}

export function watchSummary(events) {
  const n = (k) => (events || []).filter((e) => e.event_kind === k).length;
  const parts = [n("BLOCKED") && `${plural(n("BLOCKED"), "attempt")} to move held money refused`,
    n("NEW_MONEY") && `${plural(n("NEW_MONEY"), "new credit")}`,
    n("MOVED") && `${n("MOVED")} moved money that was not held`,
    n("MARKER_HOLD") && `${n("MARKER_HOLD")} caught by a marker`].filter(Boolean);
  return parts.join(" · ");
}

function Item({ e, under }) {
  return (
    <li>
      <span className="at">{clock(e.event_at)}</span>
      <span>
        {text(e, under)}
        <span className="tags">
          <span className={`pill ${e.event_kind === "BLOCKED" || e.event_kind === "MARKER_HOLD" ? "pill-held" : "pill-plain"}`}>{KIND[e.event_kind] || e.event_kind}</span>
          {e.response === "ALERT" && <span className="pill pill-alert">Officer alerted</span>}
          {e.chosen_by === "llm" && <span className="pill pill-plain" title={`Allowed: ${e.options}`}>Model's choice</span>}
        </span>
      </span>
    </li>
  );
}

// Under a decision: what this account did after the hold.
export function SinceHold({ events, limit = 6 }) {
  if (!events?.length) return null;
  const shown = events.slice(0, limit);
  return (
    <div className="since">
      <h5>Since the hold</h5>
      <ul className="watchlist">{shown.map((e) => <Item key={e.event_id} e={e} under />)}</ul>
      {events.length > limit && <p className="note">and {events.length - limit} more</p>}
    </div>
  );
}

// Review: the watch, out of hours first.
export function WatchAside({ events }) {
  if (!events?.length) return null;
  const night = events.filter((e) => e.out_of_hours);
  const dayCount = events.length - night.length;
  const alerts = events.filter((e) => e.response === "ALERT").length;
  const caught = events.filter((e) => e.event_kind === "MARKER_HOLD").reduce((s, e) => s + Number(e.hold_amount || 0), 0);
  return (
    <>
      <h3>Watched since the holds · {events.length}</h3>
      <div className="card pad">
        <p>{watchSummary(events)}.{alerts ? ` ${plural(alerts, "alert")} sent out of hours.` : ""}{caught ? ` ${inr(caught)} caught on markers.` : ""}</p>
        {night.length > 0 && <ul className="watchlist">{night.map((e) => <Item key={e.event_id} e={e} />)}</ul>}
        {dayCount > 0 && <p className="note">{plural(dayCount, "more event")} in office hours, added to the evidence on the decisions.</p>}
      </div>
    </>
  );
}