import { useEffect, useState } from "react";

export type Route = "search" | "library" | "chat" | "saved" | "label" | "eval";
const ROUTES: Route[] = ["search", "library", "chat", "saved", "label", "eval"];

function parse(): Route {
  const r = window.location.hash.replace(/^#\/?/, "").split("?")[0] as Route;
  return ROUTES.includes(r) ? r : "search";
}

export function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState<Route>(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [route, (r) => (window.location.hash = `/${r}`)];
}

export function usePoll(fn: () => void, ms: number, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const t = setInterval(fn, ms);
    return () => clearInterval(t);
  }, [fn, ms, active]);
}
