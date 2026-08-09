import { useEffect, useState } from "react";

import { VIEW_IDS, type ViewId } from "../navigation";

const VALID_VIEW_IDS = new Set<ViewId>(VIEW_IDS);

function parseHash(): ViewId {
  const raw = window.location.hash.replace(/^#\/?/, "").toLowerCase();
  return (VALID_VIEW_IDS as ReadonlySet<string>).has(raw) ? (raw as ViewId) : "home";
}

export function useHashRoute(): ViewId {
  const [viewId, setViewId] = useState<ViewId>(() => parseHash());

  useEffect(() => {
    const onHashChange = () => setViewId(parseHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return viewId;
}
