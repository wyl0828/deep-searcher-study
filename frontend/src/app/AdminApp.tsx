import { Navigate, Route, Routes } from "react-router-dom";

import type { ProductUser } from "../product-api";
import { AdminLayout } from "../layouts/AdminLayout";
import {
  AdminKnowledgeDetailPage,
  AdminKnowledgeListPage,
} from "../pages/admin/KnowledgePages";
import { AdminDiagnosticsPage } from "../pages/admin/DiagnosticsPage";
import {
  AdminRunDetailPage,
  AdminRunsPage,
} from "../pages/admin/RunsPages";
import {
  AdminAuditPage,
  AdminDashboardPage,
} from "../pages/admin/AdminPages";
import {
  AdminDepartmentPage,
  AdminUserDetailPage,
  AdminUsersPage,
} from "../pages/admin/UsersPages";

export function AdminApp({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
  return (
    <Routes>
      <Route element={<AdminLayout user={user} onLogout={onLogout} />}>
        <Route index element={<AdminDashboardPage />} />
        <Route path="knowledge" element={<AdminKnowledgeListPage />} />
        <Route path="knowledge/:knowledgeBaseId" element={<AdminKnowledgeDetailPage />} />
        {/* Compatibility alias: processing now lives in knowledge-base documents. */}
        <Route path="ingestion" element={<Navigate to="/admin/knowledge" replace />} />
        <Route path="runs" element={<AdminRunsPage />} />
        <Route path="runs/:messageId" element={<AdminRunDetailPage />} />
        <Route path="audit" element={<AdminAuditPage />} />
        <Route path="users" element={<AdminUsersPage />} />
        <Route path="users/department/unassigned" element={<AdminDepartmentPage />} />
        <Route path="users/department/:departmentId" element={<AdminDepartmentPage />} />
        <Route path="users/:userId" element={<AdminUserDetailPage />} />
        <Route path="diagnostics" element={<AdminDiagnosticsPage />} />
      </Route>
    </Routes>
  );
}
