import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, duration, inrR, joinAnd, shortName, victim as victimName } from "../format.js";
import { useLoad } from "../hooks.js";

// Demo view "The whole night" (design board "Demo: the whole night"): where one victim's money ended up across the
// three banks, in one bar that sums to the reported amount, then one lane per bank on a single time axis. No bank's
// officer has this screen: it reads every bank at once.
const n = (v) => Number(v || 0);
const mins = (ts) => { const m = /[ T](\d\d):(\d\d)/.exec(String(ts || "")); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
const between = (a, b) => mins(b) - mins(a);
const COLORS = { held: ["#5b3fa0", "#3b6ea5"], cash: ["#4f5966", "#7a828c"] };

export default function Narrator({ victim }) {
  const queue = useLoad(() => api.queue().catch(() => []), []);
  const tag = victim || (queue.data || []).find((t) => t.after_hours && t.match_status === "EXACT")?.victim_tag || (queue.data || [])[0]?.victim_tag;
  if (queue.loading && !queue.data) return <div className="wrap"><Loading what="Loading the night" /></div>;
  if (!tag) return <main id="main" className="wrap page"><h1 style={{ fontSize: 30 }}>The whole night</h1><p className="empty">Nothing has happened yet. Replay the night from the Review page.</p></main>;
  return <Night key={tag} tag={tag} queue={queue.data || []} />;
}

function Night({ tag, queue }) {
  const { data, error, loading, reload } = useLoad(() => api.narrator(tag), [tag]);
  if (loading && !data) return <div className="wrap"><Loading what="Reading all three banks" /></div>;
  if (error) return <div className="wrap"><Failed error={error} retry={reload} /></div>;
  const banks = data.trail.banks;
  const v = victimName(tag);
  const bx = banks.find((b) => b.bank_id === "BANK_X") || banks[0];
  const ticket = bx.tickets.find((t) => t.kind === "POLICE") || bx.tickets[0];
  const total = n(ticket?.amount_inr);
  const tl = data.timeline;
  const trailAccounts = new Set(banks.flatMap((b) => b.moves.flatMap((m) => [m.from, m.to])));

  // where her money ended up: held at each bank that runs the system, cash at each bank
  const parts = [];
  banks.forEach((b) => {
    if (n(b.victim_held_inr) > 0) {
      const names = b.accounts.filter((a) => n(a.held_inr) > 0).map((a) => shortName(a.name));
      const hold = tl.find((e) => e.kind === "hold" && e.bank === b.bank_id && b.accounts.some((a) => shortName(a.name) === shortName(e.account || "")));
      parts.push({ kind: "held", bank: b, amt: n(b.victim_held_inr), text: <><b>Held at {b.name}</b> in {joinAnd(names)}{hold ? `, ${clock(hold.at)}` : ""}{b.bank_id !== "BANK_X" ? ", from our onward entry" : ""}</> });
    }
  });
  const cash = [];
  banks.forEach((b) => b.moves.filter((m) => m.is_exit).forEach((m) => cash.push({ bank: b, amt: n(m.victim_inr || m.amount_inr), at: m.at, where: m.where, from: m.from })));
  tl.filter((e) => e.kind === "cash_out" && trailAccounts.has(e.account) && !cash.some((c) => c.at === e.at)).forEach((e) => cash.push({ bank: banks.find((b) => b.bank_id === e.bank), amt: n(e.amount), at: e.at, where: e.where, from: e.account }));
  cash.sort((a, b) => String(a.at).localeCompare(String(b.at)));
  const deskBank = banks.find((b) => b.works_tickets === "desk");
  const deskTicket = deskBank?.tickets?.[0];
  cash.forEach((c) => {
    const before = c.at < (ticket?.at || "");
    const closed = c.bank?.works_tickets === "desk" && deskTicket && c.at < (deskTicket.picked_up_at || "9");
    parts.push({ kind: "cash", bank: c.bank, amt: c.amt, text: <><b>Cash, {c.bank?.name}</b>, {c.where || "an ATM"}, {clock(c.at)}{before ? ", before the complaint" : closed ? `, while ${c.bank.name}'s desk was closed` : ""}</> });
  });
  const shown = parts.reduce((t, p) => t + p.amt, 0);
  const held = parts.filter((p) => p.kind === "held").reduce((t, p) => t + p.amt, 0);
  let hi = 0, ci = 0;
  parts.forEach((p) => { p.color = p.kind === "held" ? COLORS.held[Math.min(hi++, 1)] : COLORS.cash[Math.min(ci++, 1)]; });

  // the lanes
  const events = { complaints: [], ...Object.fromEntries(banks.map((b) => [b.bank_id, []])) };
  queue.filter((t) => t.after_hours && !t.from_bank && String(t.received_at).slice(0, 10) === String(ticket?.at || "").slice(0, 10) && mins(t.received_at) < 12 * 60)
    .forEach((t) => events.complaints.push({ t: t.received_at, text: `${victimName(t.victim_tag)}, ${inrR(t.amount_inr)}`, c: "#8f5400" }));
  banks.forEach((b) => {
    const E = events[b.bank_id];
    const ownHere = new Set(b.moves.filter((m) => m.hop === 1).map((m) => m.to).concat(b.accounts.map((a) => a.name)));
    const otherBank = (m) => !m.is_exit && !ownHere.has(m.to) && banks.some((o) => o.bank_id !== b.bank_id && o.moves.some((x) => x.from === m.to || x.to === m.to) || tl.some((e) => e.bank === o.bank_id && e.account === m.to));
    b.moves.filter((m) => !m.is_exit && (m.hop === 1 || (b.bank_id !== "BANK_X" && otherBank(m)))).forEach((m) => E.push({ t: m.at,
      text: m.hop === 1 && b.bank_id === "BANK_X" ? `${v === "the victim" ? "the" : "her"} ${inrR(m.amount_inr)} into ${shortName(m.to)}`
        : m.hop === 1 ? `${inrR(m.amount_inr)} into ${shortName(m.to)} from ${shortName(m.from)}` : `${inrR(m.amount_inr)} on to ${shortName(m.to)}${banks.find((o) => tl.some((e) => e.bank === o.bank_id && e.account === m.to)) ? ` at ${banks.find((o) => tl.some((e) => e.bank === o.bank_id && e.account === m.to)).name}` : ""}` }));
    b.moves.filter((m) => m.is_exit).forEach((m) => E.push({ t: m.at, text: `${inrR(m.amount_inr)} cash, ${shortName(m.from)}`, c: "#4f5966" }));
    const holds = tl.filter((e) => e.kind === "hold" && e.bank === b.bank_id && e.hold_kind === "LIEN" && trailAccounts.has(e.account));
    const firstAt = holds[0]?.at;
    const firstBatch = holds.filter((e) => e.at === firstAt && e.tag !== "NOT_LINKED");
    if (firstBatch.length) E.push({ t: firstAt, text: `held ${inrR(n(b.victim_held_inr) || firstBatch.reduce((s, e) => s + n(e.amount), 0))} in ${joinAnd(firstBatch.map((e) => shortName(e.account)))}`, c: b.bank_id === "BANK_X" ? "#5b3fa0" : "#3b6ea5" });
    holds.filter((e) => e.tag === "NOT_LINKED").forEach((e) => E.push({ t: e.at, text: `${inrR(e.amount)} lands in ${shortName(e.account)}, held`, c: "#5b3fa0" }));
    tl.filter((e) => e.kind === "refused" && e.bank === b.bank_id && trailAccounts.has(e.account)).forEach((e) => E.push({ t: e.at, text: `${shortName(e.account)} ${e.channel === "ATM" ? "cash" : "to another bank"} ${inrR(e.amount)} refused`, c: "#2f7a5a" }));
    tl.filter((e) => e.kind === "cash_out" && e.bank === b.bank_id && trailAccounts.has(e.account) && !b.moves.some((m) => m.is_exit && m.at === e.at)).forEach((e) => E.push({ t: e.at, text: `${inrR(e.amount)} cash, ${e.where || shortName(e.account)}`, c: "#7a828c" }));
    if (b.works_tickets === "desk") b.tickets.forEach((t) => E.push({ t: t.at, text: `notice from ${t.kind === "ONWARD" ? "another bank" : "the police"} arrives`, c: "#b5322a" }));
  });
  const all = Object.values(events).flat().map((e) => mins(e.t)).filter((x) => x != null);
  const deskEnd = deskTicket?.picked_up_at ? mins(deskTicket.picked_up_at) : null;
  const start = Math.floor(Math.min(...all, mins(ticket?.at) ?? 60) / 60) * 60;
  const end = Math.ceil((Math.max(...all, deskEnd ?? 0) + 30) / 60) * 60;
  const pos = (ts) => (((mins(ts) ?? start) - start) / (end - start)) * 100;
  const ticks = [];
  for (let m = start; m <= end; m += 60) ticks.push(m);
  const lay = (list) => {
    const rows = [2, 80, 22, 100], last = {};
    return [...list].sort((a, b) => String(a.t).localeCompare(String(b.t))).map((e) => {
      const x = pos(e.t);
      const top = rows.find((r) => last[r] == null || x - last[r] > 22) ?? rows[0];
      last[top] = x;
      return { ...e, x, top };
    });
  };
  const LANES = [["complaints", "Complaints", "to Bank X"], ...banks.map((b) => [b.bank_id, b.name, b.works_tickets === "desk" ? "manual desk" : "runs the system"])];

  // what the lanes show
  const sysBanks = banks.filter((b) => b.works_tickets === "agent");
  const speed = sysBanks.map((b) => {
    const h = tl.find((e) => e.kind === "hold" && e.bank === b.bank_id && trailAccounts.has(e.account));
    const t = b.tickets[0];
    if (!h || !t) return null;
    return b.bank_id === "BANK_X" ? `${b.name} held within ${duration(between(t.at, h.at))}.` : `${b.name} held within ${duration(between(t.at, h.at))} of our onward entry.`;
  }).filter(Boolean);
  const anyRefused = tl.some((e) => e.kind === "refused" && trailAccounts.has(e.account));
  const deskCash = cash.find((c) => c.bank?.works_tickets === "desk");
  const early = cash.filter((c) => c.at < (ticket?.at || ""));
  return (
    <main id="main" className="wrap" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <p className="note"><a href={`#/case/${encodeURIComponent(ticket?.case_id || "")}`}>{v === "the victim" ? "The complaint" : `${v}'s complaint`}</a> · {inrR(total)} · reported {clock(ticket?.at)}</p>
      <h1 style={{ fontSize: 30, lineHeight: 1.2, marginTop: 4 }}>Where {v === "the victim" ? "the victim's" : "her"} money ended up, across {banks.length === 3 ? "three" : banks.length} banks</h1>

      <section className="card" style={{ padding: "18px 20px", marginTop: 18 }} aria-labelledby="end-h">
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: "6px 16px", marginBottom: 10 }}>
          <h2 id="end-h" style={{ fontSize: 18 }}>{inrR(held)} of {v === "the victim" ? "the" : "her"} {inrR(total)} is held ({total ? Math.round((held / total) * 100) : 0}%)</h2>
          {Math.round(shown) === Math.round(total) && <span className="note">Adds up to {inrR(total)}</span>}
        </div>
        <div className="bar tall">{parts.map((p, i) => <div key={i} style={{ flex: i === parts.length - 1 ? "1 1 auto" : `0 0 ${(p.amt / (shown || 1)) * 100}%`, background: p.color }}>{p.kind === "held" ? `Held at ${p.bank.name}` : `Cash, ${p.bank?.name}`} {inrR(p.amt)}</div>)}</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", columnGap: 32, marginTop: 10 }}>
          {["held", "cash"].map((k) => <div key={k}>{parts.filter((p) => p.kind === k).map((p, i) => <div key={i} className="key"><span className="sw" style={{ background: p.color }} /><span>{p.text}</span><b>{inrR(p.amt)}</b></div>)}</div>)}
        </div>
      </section>

      <section className="card" style={{ padding: "18px 20px", marginTop: 22 }} aria-labelledby="lanes-h">
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "8px 16px", marginBottom: 10 }}>
          <h2 id="lanes-h" style={{ fontSize: 18 }}>The night, one lane per bank</h2>
          <p className="note" style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 28, height: 14, borderRadius: 3, background: "repeating-linear-gradient(135deg,rgba(29,42,68,0.25) 0 6px,rgba(29,42,68,0.08) 6px 12px)" }} />Desk closed, no system</p>
        </div>
        <div className="lanes">
          <div />
          <div className="axis" aria-hidden="true">{ticks.map((m) => <span key={m} style={{ left: `${((m - start) / (end - start)) * 100}%` }}>{String(Math.floor(m / 60) % 24).padStart(2, "0")}:00</span>)}</div>
          {LANES.map(([id, name, sub]) => {
            const bank = banks.find((b) => b.bank_id === id);
            const desk = bank?.works_tickets === "desk";
            const t = desk ? bank.tickets[0] : null;
            return [
              <div key={`${id}-n`} className="lname"><b>{name}</b><br /><span className="note">{sub}</span></div>,
              <div key={`${id}-l`} className="lane">
                {ticks.map((m) => <span key={m} className="gridl" style={{ left: `${((m - start) / (end - start)) * 100}%` }} />)}
                {desk && t?.picked_up_at && <span className="closed" style={{ left: 0, width: `${pos(t.picked_up_at)}%` }} />}
                <span className="line" />
                {desk && t?.picked_up_at && <span className="wait" style={{ left: `${pos(t.at)}%`, width: `${pos(t.picked_up_at) - pos(t.at)}%` }}>Notice waited {duration(between(t.at, t.picked_up_at))} for the desk to open</span>}
                {lay(events[id] || []).map((e, i) => (
                  <span key={i}>
                    <span className="ldot" style={{ left: `${e.x}%`, background: e.c || "#5b6675" }} />
                    <span className={`ev${e.x > 70 ? " right" : ""}`} style={{ left: `${e.x}%`, top: e.top }}><b>{clock(e.t)}</b>{e.text}</span>
                  </span>
                ))}
                {desk && t?.picked_up_at && <span className="ev right" style={{ left: `${pos(t.picked_up_at)}%`, top: 100 }}><b>{clock(t.picked_up_at)}</b>Desk opens it.{deskCash ? ` ${shortName(deskCash.from)} is empty.` : ""}</span>}
              </div>,
            ];
          })}
        </div>
      </section>

      <section className="card" style={{ padding: "18px 20px", marginTop: 22 }} aria-labelledby="show-h">
        <h2 id="show-h" style={{ fontSize: 18, marginBottom: 8 }}>What the lanes show</h2>
        <table className="t">
          <tbody>
            {speed.length > 0 && <tr><th scope="row" style={{ background: "transparent", width: 230 }}>Banks with the system</th><td>{speed.join(" ")}{anyRefused ? " Every later attempt to move held money was refused." : ""}</td></tr>}
            {deskBank && deskTicket && <tr><th scope="row" style={{ background: "transparent" }}>The bank without it</th><td>{deskBank.name} received the notice at {clock(deskTicket.at)}.{deskCash ? ` Its desk was closed, so the ${inrR(deskCash.amt)} left as cash at ${clock(deskCash.at)}, ${duration(between(deskTicket.at, deskCash.at))} later,` : ""} {deskTicket.picked_up_at ? `and the notice sat until ${clock(deskTicket.picked_up_at)}.` : "and the notice is still waiting."}</td></tr>}
            {early.length > 0 && <tr><th scope="row" style={{ background: "transparent" }}>What was never reachable</th><td>{joinAnd(early.map((c) => `${inrR(c.amt)} cash at ${clock(c.at)}`))}, {duration(between(early[0].at, ticket.at))} before {v === "the victim" ? "the victim" : v} called. No system could have held it.</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}