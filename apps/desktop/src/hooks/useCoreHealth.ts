import { useQuery } from "@tanstack/react-query";

import { fetchCoreHealth } from "../api/core";

export const CORE_HEALTH_QUERY_KEY = ["system", "core-health"] as const;

export function useCoreHealth() {
  return useQuery({
    queryKey: CORE_HEALTH_QUERY_KEY,
    queryFn: ({ signal }) => fetchCoreHealth(signal),
    retry: false,
  });
}
