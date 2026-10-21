import { api } from "../api.js";
import { duration, inr } from "../format.js";
import { useLoad } from "../hooks.js";
import { Failed, Loading } from "../components/bits.jsx";
import NightStrip from "../components/NightStrip.jsx";

// Today's desk vs the agent, on the same ticket and the same transactions, computed in Exasol.
export default function Compare({ caseId }) {
  const { data, error, loading, reload } = useLoad(() => api.compare(caseId, true), [caseId]);
  if (loading && !data) return <Loading what="Replaying today's desk in Exasol" />;
  if (error) return <Failed error={error} retry={reload} />;
  const d = data;
  const opens = (/(\d{2}:\d{2})(:\d{2})?$/.exec(d.desk.desk_opens) || [, "10:00"])[1];
  const today = [
    { at: d.received_at, label: "ticket lands", kind: "ticket" },
    ...d.walked_out_after_ticket.map((w) => ({ at: w.at, label: `${inr(w.amount_inr)} out of ${w.name}`, kind: "lost" })),
    ...d.today.accounts.map((a) => ({ at: a.action_at, label: a.action === "WHOLE_FREEZE" ? `${a.name} frozen whole` : `${a.name}: empty`, kind: a.action === "WHOLE_FREEZE" ? "freeze" : "desk" })),
  ];
  const tool = [
    { at: d.received_at, label: "ticket lands", kind: "ticket" },
    { at: d.tool.first_hold_at, label: `${d.tool.holds_overnight.length} holds, onward entry filed`, kind: "hold" },
    ...d.walked_out_after_ticket.map((w) => ({ at: w.at, label: `${w.name}'s cash-out refused`, kind: "stopped" })),
  ];
  const rows = [
    ["Victim's money held at this bank", inr(d.today.victim_held_inr), inr(d.tool.victim_held_inr), `of ${inr(d.victim_paid_inr)} reported`],
    ["Honest money locked", inr(d.today.honest_locked_inr), <>{inr(d.tool.honest_locked_overnight_inr)} overnight,<br />{inr(d.tool.honest_locked_now_inr)} after your review</>, "money that is not any victim's"],
    ["Ticket to first action", duration(d.today.minutes_to_first_hold), duration(d.tool.minutes_to_first_hold), ""],
    ["Next bank gets its ticket", d.today.onward_filed_at?.slice(11, 16) || "–", d.tool.onward_filed_at?.slice(11, 16) || "–", "onward entry filed"],
  ];
  return (
    <>
      <header className="page-head">
        <div>
          <p className="crumb"><a href={`#/case/${caseId}`}>{caseId}</a> / compare</p>
          <h1>The same night, two ways</h1>
          <p className="lede">Today the ticket waits for the desk to open at {opens}, and the desk freezes whatever it finds. With the agent, the holds the rules require land minutes after the ticket.</p>
        </div>
      </header>
      <NightStrip deskOpens={opens} lanes={[{ name: "Today's desk", events: today }, { name: "With the agent", events: tool }]} />
      <div className="table-wrap">
        <table className="table scoreboard">
          <thead><tr><th>On this ticket</th><th className="num">Today's desk</th><th className="num">With the agent</th><th /></tr></thead>
          <tbody>
            {rows.map(([label, a, b, note]) => (
              <tr key={label}><th scope="row">{label}</th><td className="num">{a}</td><td className="num strong">{b}</td><td className="quiet">{note}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="quiet">Today's desk is simulated: it opens at {opens}, takes {d.desk.first_lookup_minutes} minutes for the first lookup and {d.desk.per_account_minutes} for each further account, and freezes whole accounts. A real bank sees only its own accounts; the next bank's hold depends on how that bank works its ticket.</p>
    </>
  );
}