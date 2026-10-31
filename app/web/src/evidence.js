// The account's 90-day history in plain words, from the nine v2 signals the database computed (D-57).
// The words describe; the rules engine decides. Nothing here changes an amount.
import { age, stay } from "./format.js";

const byName = (signals) => Object.fromEntries((signals || []).map((s) => [s.signal_name, s.signal_value]));
const pct = (v) => `${Math.round(Number(v || 0) * 100)}%`;
export const TYPE_NAMES = { PIPE_FOR_FRAUD: "Pipe for fraud", FAST_MOVER: "Fast mover", EXPOSED_BUSINESS: "Exposed business",
  ORDINARY_CUSTOMER: "Ordinary customer", GREY_ZONE: "Grey zone" };

// Rows for the "What the history shows" table on the case page.
export function evidenceRows(signals, openedOn, at) {
  const v = byName(signals);
  const rows = [];
  if (v.median_dwell_minutes != null) rows.push(["Money usually stays in the account", stay(v.median_dwell_minutes)]);
  if (v.passed_on_within_hour != null) rows.push(["Sent on within an hour", pct(v.passed_on_within_hour)]);
  if (v.cashed_out_within_hour != null) rows.push(["Cash or crypto within an hour", pct(v.cashed_out_within_hour)]);
  if (v.top3_payer_share != null)
    rows.push(["Payments come from", v.top3_payer_share < 0.2 ? "Many small payers" : v.top3_payer_share > 0.8 ? "A few large payers" : "A mix of payers"]);
  if (v.money_from_flagged_share) rows.push(["Money from flagged accounts", pct(v.money_from_flagged_share)]);
  if (v.direct_complaints_7d) rows.push(["Fraud complaints that paid it in 7 days", String(Math.round(v.direct_complaints_7d))]);
  if (openedOn) rows.push(["Account age (context, not scored)", age(openedOn, at)]);
  return rows;
}

// Bullets for the genuine-customer card on Review, in the console design's words and order.
const longStay = (m) => (m >= 90 ? `about ${Math.round(m / 60)} hours` : `about ${Math.round(m)} minutes`);
export function evidenceBullets(signals, inflows) {
  const v = byName(signals);
  const out = [];
  if (v.median_dwell_minutes != null) out.push(`Money usually stays in the account for ${longStay(v.median_dwell_minutes)}`);
  if (v.passed_on_within_hour != null) out.push(`${v.passed_on_within_hour < 0.5 ? "Only " : ""}${pct(v.passed_on_within_hour)} of incoming money goes out within an hour`);
  if (v.top3_payer_share != null && v.top3_payer_share < 0.2) out.push("Receives many small payments, like a shop");
  if (v.cashed_out_within_hour != null) out.push(v.cashed_out_within_hour ? `${pct(v.cashed_out_within_hour)} taken out as cash or crypto within an hour` : "No cash or crypto taken out right after money arrives");
  const linked = inflows || [];
  if (linked.length === 1) out.push(`Only one payment linked to fraud: ₹${Number(linked[0].amount_inr).toLocaleString("en-IN")} from ${linked[0].from_name} at ${String(linked[0].ts).slice(11, 16)}`);
  else if (linked.length > 1) out.push(`${linked.length} payments linked to fraud on this trail`);
  return out;
}

// One line for a decision list: "Fast mover · pass-through 0.85 · fraud exposure 0.13"
export function evidenceLine(p, at) {
  const parts = [];
  if (p.opened_on) {
    const days = Math.round((new Date(String(at || p.received_at || "2026-10-18").slice(0, 10)) - new Date(String(p.opened_on).slice(0, 10))) / 86400000);
    parts.push(days >= 365 ? `${age(p.opened_on, at || p.received_at)} old` : `${days} days old`);
  }
  if (p.account_pattern) parts.push(TYPE_NAMES[p.account_pattern] || p.account_pattern);
  if (p.pass_through_score != null) parts.push(`pass-through ${Number(p.pass_through_score).toFixed(2)}`);
  if (p.exposure_score != null) parts.push(`fraud exposure ${Number(p.exposure_score).toFixed(2)}`);
  return parts.join(" · ");
}

export function evidenceTitle(pattern) {
  if (pattern === "ORDINARY_CUSTOMER") return "Why this looks like an ordinary customer";
  if (pattern === "PIPE_FOR_FRAUD" || pattern === "FAST_MOVER") return "Why money looks like it runs through this account";
  return "What the account's history shows";
}