import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2_000,
      gcTime: 60_000,
      retry: false,
    },
  },
});

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("ZANA desktop root element is missing.");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
