import { createContext, useEffect, useLayoutEffect, useRef, useState } from "react";

// The panel's pinned footer. A card inside the panel puts its decision buttons here (DecisionCard, PinnedActions),
// so they stay on screen while the officer scrolls through the evidence.
export const PanelFooter = createContext(null);

// The panel keeps clear of `beside` (the list it came from) when there is room: it takes the space to the right of
// it, between these widths, so the list's Open buttons stay visible. With less room it lies over the page as usual.
const MIN_W = 400, MAX_W = 640, GAP = 16;

// A panel on the right for one decision. It lies over the page (the right-hand column is not needed while
// deciding) and is not modal: the officer can still scroll the list and click another row, which swaps what the
// panel shows. Esc or Close shuts it, and the
// keyboard focus goes back to the button that opened it. `itemKey` changes when the panel moves to another
// decision, so the heading takes the focus again and a screen reader announces the new one.
export default function SidePanel({ title, itemKey, onClose, beside, children }) {
  const panel = useRef(null);
  const heading = useRef(null);
  const opener = useRef(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [width, setWidth] = useState(null);
  const [footer, setFooter] = useState(null);     // the footer element, once it exists

  // fit beside the list: measured before paint and again whenever the window is resized
  useLayoutEffect(() => {
    const fit = () => {
      const el = beside?.current;
      const room = el ? window.innerWidth - el.getBoundingClientRect().right - GAP : 0;
      setWidth(room >= MIN_W ? Math.min(room, MAX_W) : null);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [beside]);

  // once per opening: remember the opener, listen for Esc, and give the focus back on close
  useEffect(() => {
    opener.current = document.activeElement;
    const onKey = (e) => { if (e.key === "Escape") close.current(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const back = opener.current;
      if (back && document.contains(back)) back.focus();
    };
  }, []);

  // a new decision in the panel: if another row's button opened it, focus goes back there on close
  useEffect(() => {
    const now = document.activeElement;
    if (now && now !== document.body && panel.current && !panel.current.contains(now)) opener.current = now;
    heading.current?.focus();
  }, [itemKey]);

  return (
    <aside className="sidepanel" ref={panel} role="dialog" aria-modal="false" aria-labelledby="sidepanel-h"
      style={width ? { width } : undefined}>
      <div className="sp-head">
        <h2 id="sidepanel-h" ref={heading} tabIndex={-1}>{title}</h2>
        <button className="btn btn-s" type="button" onClick={onClose}>Close <span aria-hidden="true">✕</span></button>
      </div>
      <div className="sp-body"><PanelFooter.Provider value={footer}>{children}</PanelFooter.Provider></div>
      <div className="sp-foot" ref={setFooter} />
    </aside>
  );
}