// The account's 90-day history in plain words, from the eight signals the database computed.
// The words describe; the rules engine decides. Nothing here changes an amount.
import { age, stay } from "./format.js";

const byName = (signals) => Object.fromEntries((signals || []).map((s) => [s.signal_name, s.signal_value]));

// Rows for the "Why it looks like ..." table on the case page.
export function evidenceRows(signals, openedOn, at) {
  const v = byName(signals);
  const rows = [];
  if (v.median_dwell_minutes != null) rows.push(["Money usually stays in the account", stay(v.median_dwell_minutes)]);
  if (v.pass_through_ratio != null) rows.push(["Goes out within an hour", `${Math.round(v.pass_through_ratio * 100)}%`]);
  if (v.large_payer_share != null)
    rows.push(["Payments come from", v.large_payer_share < 0.2 ? "Many small payers" : v.large_payer_share > 0.8 ? "A few large payers" : "A mix of payers"]);
  if (v.cash_or_crypto_exits != null) rows.push(["Cash or crypto withdrawals", v.cash_or_crypto_exits ? String(Math.round(v.cash_or_crypto_exits)) : "None"]);
  if (v.complaint_linked_credits_7d) rows.push(["Fraud complaints that paid it in 7 days", String(Math.round(v.complaint_linked_credits_7d))]);
  if (openedOn) rows.push(["Account age", age(openedOn, at)]);
  return rows;
}

// Bullets for the genuine-customer card on Review, in the console design's words and order.
const longStay = (m) => (m >= 90 ? `about ${Math.round(m / 60)} hours` : `about ${Math.round(m)} minutes`);
export function evidenceBullets(signals, inflows) {
  const v = byName(signals);
  const out = [];
  if (v.median_dwell_minutes != null) out.push(`Money usually stays in the account for ${longStay(v.median_dwell_minutes)}`);
  if (v.pass_through_ratio != null) out.push(`${v.pass_through_ratio < 0.5 ? "Only " : ""}${Math.round(v.pass_through_ratio * 100)}% of incoming money goes out within an hour`);
  if (v.large_payer_share != null && v.large_payer_share < 0.2) out.push("Receives many small payments, like a shop");
  if (v.cash_or_crypto_exits != null) out.push(v.cash_or_crypto_exits ? `${Math.round(v.cash_or_crypto_exits)} cash or crypto withdrawals` : "No cash or crypto withdrawals");
  const linked = inflows || [];
  if (linked.length === 1) out.push(`Only one payment linked to fraud: ₹${Number(linked[0].amount_inr).toLocaleString("en-IN")} from ${linked[0].from_name} at ${String(linked[0].ts).slice(11, 16)}`);
  else if (linked.length > 1) out.push(`${linked.length} payments linked to fraud on this trail`);
  return out;
}

// One line for the mule table: "20 days old · 88% leaves within 1 h · 3 cash or crypto withdrawals"
export function evidenceLine(p, at) {
  const parts = [];
  if (p.opened_on) {
    const days = Math.round((new Date(String(at || p.received_at || "2026-10-18").slice(0, 10)) - new Date(String(p.opened_on).slice(0, 10))) / 86400000);
    parts.push(days >= 365 ? `${age(p.opened_on, at || p.received_at)} old` : `${days} days old`);
  }
  if (p.pass_through != null) parts.push(`${Math.round(p.pass_through * 100)}% leaves within 1 h`);
  if (p.cash_exits != null) parts.push(`${Math.round(p.cash_exits)} cash or crypto withdrawal${Math.round(p.cash_exits) === 1 ? "" : "s"}`);
  if (p.complaint_links > 1) parts.push(`paid by ${Math.round(p.complaint_links)} fraud complaints this week`);
  return parts.join(" · ");
}

export function evidenceTitle(band) {
  if (band === "HONEST") return "Why this looks like a genuine business";
  if (band === "MULE") return "Why this looks like a pass-through account";
  return "What the account's history shows";
}