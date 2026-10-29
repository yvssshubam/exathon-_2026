// Display helpers. Amounts use Indian grouping (₹1,19,500); times are shown as stored (IST).
const rupees = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

// Whole rupees, for headline figures.
export function inrR(value) {
  return value === null || value === undefined || value === "" ? "–" : inr(Math.round(Number(value)));
}

export function inr(value) {
  if (value === null || value === undefined || value === "") return "–";
  return "₹" + rupees.format(Number(value));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-10-18 02:13:00" -> "18 Oct 2026, 02:13"
export function when(ts) {
  if (!ts) return "–";
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(ts));
  if (!m) return String(ts);
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}, ${m[4]}:${m[5]}`;
}

// "2026-10-18 02:13:00" -> "18 Oct, 02:13": date and time without the year, for table rows
export function dayTime(ts) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(ts || ""));
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}, ${m[4]}:${m[5]}` : "–";
}

// "2026-10-18 02:13:00" -> "02:13"
export function clock(ts) {
  const m = /[ T](\d{2}):(\d{2})/.exec(String(ts || ""));
  return m ? `${m[1]}:${m[2]}` : "–";
}

// minutes since midnight of a timestamp, for the night strip
export function minuteOfDay(ts) {
  const m = /[ T](\d{2}):(\d{2})/.exec(String(ts || ""));
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

export function duration(minutes) {
  if (minutes === null || minutes === undefined) return "–";
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function shortAccount(id) {
  if (!id) return "";
  if (id.startsWith("EXIT:")) return id.split(":").slice(1).join(" ");
  return id.replace(/^ACC_/, "");
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || one + "s"}`;
}

// Age of an account in plain words, from its opening date to a moment ("2026-10-18 02:16:00").
export function age(openedOn, at) {
  if (!openedOn) return null;
  const days = Math.round((new Date(String(at || "2026-10-18").slice(0, 10)) - new Date(String(openedOn).slice(0, 10))) / 86400000);
  if (days >= 365) {
    const years = Math.round((days / 365) * 10) / 10;
    return `${Number.isInteger(years) ? years : years.toFixed(1)} year${years === 1 ? "" : "s"}`;
  }
  return `${days} day${days === 1 ? "" : "s"}`;
}

export function ageDays(openedOn, at) {
  if (!openedOn) return null;
  return Math.round((new Date(String(at || "2026-10-18").slice(0, 10)) - new Date(String(openedOn).slice(0, 10))) / 86400000);
}

// 469 -> "About 8 h"; 14 -> "About 14 min"
export function stay(minutes) {
  if (minutes === null || minutes === undefined) return "–";
  if (minutes >= 90) return `About ${Math.round(minutes / 60)} h`;
  return `About ${Math.round(minutes)} min`;
}

export function hoursRange(minutes) {
  if (!minutes.length) return "–";
  const hs = minutes.map((m) => Math.round(m / 60));
  const lo = Math.min(...hs), hi = Math.max(...hs);
  if (hi < 1) return `${Math.min(...minutes)} to ${Math.max(...minutes)} min`;
  if (lo < 1) return `${Math.min(...minutes)} min to ${hi} h`;
  return lo === hi ? `${lo} hour${lo === 1 ? "" : "s"}` : `${lo} to ${hi} hours`;
}
// "V_LAKSHMI" -> "Lakshmi"
export function victim(tag) {
  if (!tag) return "";
  const t = String(tag).replace(/^V_/, "");
  if (/\d/.test(t)) return `Victim ${t}`;            // generated cases: "X03" -> "Victim X03"
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
}

// Account names as the officer reads them: never a "mule" label ("Mule 07" -> "Account M07").
export function accountName(name, id) {
  if (!name) return (id || "").replace(/^ACC_/, "");
  return String(name).replace(/^Mule (\d+)$/, "Account M$1");
}

// "Murugan (Sri Murugan Fuels)" -> "Murugan"; "Account M07" -> "M07"
export function shortName(name) {
  return accountName(name).replace(/ \(.*\)$/, "").replace(/^Account (?=M\d)/, "");
}

// Account age as the cards say it: "68 days", "3 years", "1 year 8 months"
export function ageWords(openedOn, at) {
  if (!openedOn) return "";
  const a = new Date(String(openedOn).slice(0, 10)), b = new Date(String(at || "2026-10-18").slice(0, 10));
  const days = Math.round((b - a) / 86400000);
  if (days < 365) return `${days} day${days === 1 ? "" : "s"}`;
  let months = (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth() - (b.getDate() < a.getDate() ? 1 : 0);
  const y = Math.floor(months / 12), m = months % 12;
  return `${y} year${y === 1 ? "" : "s"}${m ? ` ${m} month${m === 1 ? "" : "s"}` : ""}`;
}

// Backend sentences say "Rs 2,00,000"; the console shows "₹2,00,000"
export const rs = (s) => String(s ?? "").replace(/Rs\.? ?(?=[\d-])/g, "₹");

// "₹1,92,900" whole rupees, "−₹70,000" for money out
export function inrSigned(v) {
  const n = Math.round(Number(v || 0));
  return n < 0 ? `−${inrR(-n)}` : `+${inrR(n)}`;
}

export const joinAnd = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);
export const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const hhmm = clock;