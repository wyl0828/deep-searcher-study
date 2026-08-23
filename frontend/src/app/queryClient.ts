import { QueryClient } from "@tanstack/react-query";

export const workspaceQueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
});
