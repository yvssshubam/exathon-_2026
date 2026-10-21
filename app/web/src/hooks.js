import { useCallback, useEffect, useState } from "react";

// Load data from the API; `reload` fetches again after an action.
export function useLoad(fn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    fn()
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => setState({ data: null, error, loading: false }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(load, [load]);
  return { ...state, reload: load };
}

// The officer's name, remembered on this device only.
export function useOfficer() {
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem("ph.officer") || "";
    } catch {
      return "";
    }
  });
  const save = (value) => {
    setName(value);
    try {
      localStorage.setItem("ph.officer", value);
    } catch {
      /* private window: keep it in memory */
    }
  };
  return [name, save];
}

// "#/case/CASE_DEMO" -> ["case", "CASE_DEMO"]
export function useRoute() {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "review").split("/").map(decodeURIComponent);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}