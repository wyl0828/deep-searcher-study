import {
  ArrowRightStartOnRectangleIcon,
  BookOpenIcon,
  ChartBarIcon,
  ClipboardDocumentListIcon,
  Cog6ToothIcon,
  ShieldCheckIcon,
  UserGroupIcon,
} from "@heroicons/react/24/outline";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import type { ProductUser } from "../product-api";

export function AdminLayout({
  user,
  onLogout,
}: {
  user: ProductUser;
  onLogout: () => void;
}) {
  const navigate = useNavigate();
  return (
    <div className="product-shell admin-shell">
      <a className="skip-link" href="#admin-main">
        跳到主要内容
      </a>
      <aside className="product-sidebar admin-sidebar">
        <Link className="brand-lockup" to="/admin" aria-label="DeepSearcher 管理后台首页">
          <img src="/deepsearcher-logo.png" alt="DeepSearcher" />
        </Link>

        <div className="admin-sidebar-title">
          <ShieldCheckIcon aria-hidden="true" />
          <span>运营管理</span>
        </div>

        <nav className="primary-navigation admin-navigation" aria-label="管理后台导航">
          <span className="admin-nav-group-label">运营总览</span>
          <NavLink to="/admin" end>
            <ChartBarIcon aria-hidden="true" />
            概览
          </NavLink>

          <span className="admin-nav-group-label">知识管理</span>
          <NavLink to="/admin/knowledge">
            <BookOpenIcon aria-hidden="true" />
            知识库
          </NavLink>

          <span className="admin-nav-group-label">用户与权限</span>
          <NavLink to="/admin/users">
            <UserGroupIcon aria-hidden="true" />
            用户管理
          </NavLink>

          <span className="admin-nav-group-label">回答质量</span>
          <NavLink to="/admin/runs">
            <ChartBarIcon aria-hidden="true" />
            回答记录
          </NavLink>

          <span className="admin-nav-group-label">系统管理</span>
          <NavLink to="/admin/diagnostics">
            <Cog6ToothIcon aria-hidden="true" />
            系统状态
          </NavLink>
          <NavLink to="/admin/audit">
            <ClipboardDocumentListIcon aria-hidden="true" />
            操作审计
          </NavLink>
        </nav>

        <div className="sidebar-footer admin-sidebar-footer">
          <ShieldCheckIcon aria-hidden="true" />
          <span title={user.username}>{user.display_name}</span>
          <button type="button" aria-label="退出登录" onClick={onLogout}>
            <ArrowRightStartOnRectangleIcon aria-hidden="true" />
          </button>
        </div>
      </aside>

      <main className="product-main admin-main" id="admin-main" tabIndex={-1}>
        <header className="product-topbar admin-topbar">
          <div className="admin-breadcrumb">
            <ShieldCheckIcon aria-hidden="true" />
            <span>DeepSearcher 管理后台</span>
          </div>
          <button
            className="secondary-button admin-return-button"
            type="button"
            onClick={() => navigate("/")}
          >
            返回问答
          </button>
        </header>
        <Outlet />
      </main>
    </div>
  );
}
