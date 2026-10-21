// Display helpers. Amounts use Indian grouping (₹1,19,500); times are shown as stored (IST).
const rupees = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

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