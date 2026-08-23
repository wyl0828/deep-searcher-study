import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import type { ProductUser } from "../product-api";
import { ForbiddenState } from "../components/states";
import { AdminApp } from "./AdminApp";
import { RequireAdmin } from "./RequireAdmin";
import { UserApp } from "./UserApp";

function ConsoleRedirect() {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={{
        pathname: "/admin/diagnostics",
        search: location.search,
        hash: location.hash,
      }}
    />
  );
}

function LegacyUsersRoute({ user }: { user: ProductUser }) {
  if (user.role !== "admin") {
    return <ForbiddenState description="用户管理属于管理员范围，当前账号没有访问权限。" />;
  }
  const location = useLocation();
  return (
    <Navigate
      replace
      to={{ pathname: "/admin/users", search: location.search, hash: location.hash }}
    />
  );
}

export function AppRouter({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
  return (
    <Routes>
      <Route
        path="/admin/*"
        element={
          <RequireAdmin user={user}>
            <AdminApp user={user} onLogout={onLogout} />
          </RequireAdmin>
        }
      />
      <Route path="/console" element={<ConsoleRedirect />} />
      <Route path="/users" element={<LegacyUsersRoute user={user} />} />
      <Route path="/*" element={<UserApp user={user} onLogout={onLogout} />} />
    </Routes>
  );
}
