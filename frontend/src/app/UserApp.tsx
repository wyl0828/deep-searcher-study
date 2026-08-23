import { Navigate, Route, Routes } from "react-router-dom";

import type { ProductUser } from "../product-api";
import { UserLayout } from "../layouts/UserLayout";
import { ChatPage, NewChatPage } from "../pages/chat/ChatPages";

export function UserApp({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
  return (
    <Routes>
      <Route element={<UserLayout user={user} onLogout={onLogout} />}>
        <Route index element={<NewChatPage />} />
        <Route path="chat/:conversationId" element={<ChatPage isAdmin={user.role === "admin"} />} />
        <Route path="knowledge/*" element={<Navigate to="/" replace />} />
        <Route path="workspaces/*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
