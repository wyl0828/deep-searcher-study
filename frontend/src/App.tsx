import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";

import { AuthenticatedWorkspace } from "./app/AuthenticatedWorkspace";
import { workspaceQueryClient } from "./app/queryClient";

export { workspaceQueryClient } from "./app/queryClient";

export function App() {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      <AuthenticatedWorkspace />
    </QueryClientProvider>
  );
}

export function TestProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}
