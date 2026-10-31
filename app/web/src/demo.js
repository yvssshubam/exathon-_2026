import { useSyncExternalStore } from "react";

// Whether the demo-only text is shown: the "Manual process" comparisons and how they are measured. Judges switch it
// on from the Demo mode button; an officer's screen never needs it. Kept per browser, so a reload keeps the choice.
const KEY = "ph_show_manual";
const listeners = new Set();

function read() {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

let shown = read();

export function setShowManual(on) {
  shown = on;
  try {
    localStorage.setItem(KEY, on ? "1" : "0");
  } catch {
    // storage can be unavailable (a private window); the switch still works for this visit
  }
  listeners.forEach((fn) => fn());
}

export function useShowManual() {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => listeners.delete(fn); }, () => shown);
}