import { QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";

import { ErrorState, LoadingState } from "../components/states";
import { getAuthStatus, logoutWorkspace } from "../product-api";
import { AppRouter } from "./AppRouter";
import { AuthScreen } from "./AuthScreen";
import { workspaceQueryClient } from "./queryClient";

export function AuthenticatedWorkspace() {
  const queryClient = useQueryClient();
  const auth = useQuery({
    queryKey: ["auth-status"],
    queryFn: getAuthStatus,
    retry: false,
  });
  const logout = useMutation({
    mutationFn: logoutWorkspace,
    onSuccess: () => {
      queryClient.clear();
      queryClient.setQueryData(["auth-status"], {
        setup_required: false,
        authenticated: false,
        user: null,
      });
    },
  });

  if (auth.isLoading) return <LoadingState label="正在检查登录状态…" />;
  if (auth.error) return <ErrorState message={auth.error.message} />;
  if (!auth.data?.authenticated || !auth.data.user) {
    return <AuthScreen setupRequired={Boolean(auth.data?.setup_required)} />;
  }
  return (
    <BrowserRouter>
      <AppRouter user={auth.data.user} onLogout={() => logout.mutate()} />
    </BrowserRouter>
  );
}

export function AuthenticatedWorkspaceTestBoundary({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}
