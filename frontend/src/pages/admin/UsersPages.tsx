import { PlusIcon, UserCircleIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  createDepartment,
  createUser,
  getCompanyWideKnowledgeAccess,
  getDepartmentKnowledgeAccess,
  getUserKnowledgeAccess,
  listAdminDepartments,
  listAdminKnowledgeBases,
  listAdminUsers,
  listDepartmentUsers,
  setCompanyWideKnowledgeAccess,
  setDepartmentKnowledgeAccess,
  setUserKnowledgeAccess,
  updateUser,
  type Department,
  type InheritedKnowledgeAccess,
  type ProductUser,
  type UserKnowledgeAccess,
} from "../../product-api";
import { EmptyState, ErrorState, LoadingState } from "../../components/states";
import "../../workspace.css";

export const ADMIN_USER_TABS = ["users", "departments", "company-wide"] as const;
export type AdminUsersTab = (typeof ADMIN_USER_TABS)[number];

export function normalizeAdminUsersTab(value: string | null): AdminUsersTab {
  return ADMIN_USER_TABS.includes(value as AdminUsersTab)
    ? (value as AdminUsersTab)
    : "users";
}

function departmentLabel(department: ProductUser["department_name"]): string {
  return department || "未分部门";
}

function visibleKnowledgeBases(items: Array<{ id: string; name: string }>) {
  return items
    .filter((item) => item.name !== "__legacy__" && !item.name.includes("__legacy__"))
    .map((item) => ({ id: item.id, name: item.name }));
}

function KnowledgeCheckboxes({
  knowledgeBases,
  selected,
  onChange,
}: {
  knowledgeBases: Array<{ id: string; name: string }>;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const selectedSet = new Set(selected);
  return (
    <div className="admin-access-checklist">
      {knowledgeBases.length === 0 ? (
        <p className="admin-muted">暂无可配置的知识库。</p>
      ) : (
        knowledgeBases.map((knowledgeBase) => (
          <label className="admin-access-check" key={knowledgeBase.id}>
            <input
              type="checkbox"
              checked={selectedSet.has(knowledgeBase.id)}
              onChange={(event) => {
                const next = new Set(selectedSet);
                if (event.target.checked) next.add(knowledgeBase.id);
                else next.delete(knowledgeBase.id);
                onChange(Array.from(next));
              }}
            />
            <span>{knowledgeBase.name}</span>
          </label>
        ))
      )}
    </div>
  );
}

function sourceLabel(source: InheritedKnowledgeAccess["sources"][number]): string {
  return source === "company_wide" ? "全员" : "部门";
}

function InheritedAccessList({ items }: { items: InheritedKnowledgeAccess[] }) {
  if (!items.length) {
    return <p className="admin-muted">当前没有全员或部门继承访问。</p>;
  }
  return (
    <div className="admin-inherited-access-list">
      {items.map((item) => (
        <div className="admin-inherited-access-item" key={item.knowledge_base_id}>
          <strong>{item.knowledge_base_name}</strong>
          <span className="admin-access-source-list">
            {item.sources.map((source) => (
              <em key={source}>{sourceLabel(source)}</em>
            ))}
          </span>
        </div>
      ))}
    </div>
  );
}

function UserRows({ users }: { users: ProductUser[] }) {
  if (!users.length) {
    return <EmptyState title="暂无用户" description="这个范围内还没有普通业务账号。" />;
  }
  return (
    <div className="admin-data-table-wrap">
      <table className="admin-data-table">
        <thead>
          <tr>
            <th>用户</th>
            <th>部门</th>
            <th>角色</th>
            <th>可访问知识</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>
                <span className="admin-object-link">
                  <UserCircleIcon aria-hidden="true" />
                  <span>
                    <strong>{user.display_name}</strong>
                    <small>@{user.username}</small>
                  </span>
                </span>
              </td>
              <td>{departmentLabel(user.department_name)}</td>
              <td>{user.role === "admin" ? "管理员" : "普通用户"}</td>
              <td>
                {user.accessible_knowledge_base_count == null
                  ? "未记录"
                  : `${user.accessible_knowledge_base_count} 个知识库`}
              </td>
              <td>{user.is_active ? "正常" : "已停用"}</td>
              <td>
                <Link className="secondary-button compact" to={`/admin/users/${user.id}`}>
                  查看
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UserManagementTabs({ active }: { active: AdminUsersTab }) {
  const tabs: Array<[AdminUsersTab, string]> = [
    ["users", "用户"],
    ["departments", "部门"],
    ["company-wide", "全员知识"],
  ];
  return (
    <nav className="admin-page-tabs" aria-label="用户管理范围">
      {tabs.map(([tab, label]) => (
        <Link
          className={active === tab ? "active" : ""}
          to={`/admin/users?tab=${tab}`}
          key={tab}
          aria-current={active === tab ? "page" : undefined}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}

export function AdminUsersPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab = normalizeAdminUsersTab(rawTab);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [departmentName, setDepartmentName] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [departmentId, setDepartmentId] = useState("");

  useEffect(() => {
    if (rawTab === tab) return;
    const next = new URLSearchParams(searchParams);
    next.set("tab", tab);
    setSearchParams(next, { replace: true });
  }, [rawTab, searchParams, setSearchParams, tab]);

  const departments = useQuery({
    queryKey: ["admin-departments"],
    queryFn: listAdminDepartments,
    enabled: tab === "departments" || (tab === "users" && showCreate),
  });
  const knowledgeBases = useQuery({
    queryKey: ["admin-knowledge-bases"],
    queryFn: listAdminKnowledgeBases,
    enabled: tab === "company-wide",
  });
  const users = useQuery({
    queryKey: ["admin-users", search],
    queryFn: () => listAdminUsers({ q: search || undefined, page_size: 100 }),
    enabled: tab === "users",
  });
  const companyAccess = useQuery({
    queryKey: ["admin-company-wide-access"],
    queryFn: getCompanyWideKnowledgeAccess,
    enabled: tab === "company-wide",
  });
  const [companySelected, setCompanySelected] = useState<string[] | null>(null);
  const companyIds = companySelected ?? companyAccess.data?.knowledge_base_ids ?? [];

  const createDepartmentMutation = useMutation({
    mutationFn: () => createDepartment(departmentName),
    onSuccess: () => {
      setDepartmentName("");
      void queryClient.invalidateQueries({ queryKey: ["admin-departments"] });
    },
  });
  const companyMutation = useMutation({
    mutationFn: () => setCompanyWideKnowledgeAccess(companyIds),
    onSuccess: (result) => {
      setCompanySelected(result.knowledge_base_ids);
      void queryClient.invalidateQueries({ queryKey: ["admin-knowledge-bases"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-company-wide-access"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const createUserMutation = useMutation({
    mutationFn: () =>
      createUser({
        username,
        display_name: displayName,
        password,
        role,
        department_id: departmentId || null,
      }),
    onSuccess: () => {
      setUsername("");
      setDisplayName("");
      setPassword("");
      setRole("member");
      setDepartmentId("");
      setShowCreate(false);
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-departments"] });
    },
  });

  const departmentItems = departments.data?.items ?? [];
  const allKnowledgeBases = useMemo(
    () => visibleKnowledgeBases(knowledgeBases.data ?? []),
    [knowledgeBases.data],
  );

  return (
    <section className="admin-page admin-users-page">
      <header className="admin-page-heading admin-page-heading--with-action">
        <div>
          <span className="eyebrow">用户与权限</span>
          <h1>用户管理</h1>
          <p>用户、部门和全员知识分开管理；个人额外知识在用户详情中配置。</p>
        </div>
        {tab === "users" ? (
          <button className="product-primary-button" type="button" onClick={() => setShowCreate(true)}>
            <PlusIcon aria-hidden="true" /> 添加用户
          </button>
        ) : null}
      </header>

      <UserManagementTabs active={tab} />

      {showCreate ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-card admin-user-modal" role="dialog" aria-modal="true">
            <header className="modal-header">
              <div><span className="eyebrow">用户与权限</span><h2>添加用户</h2></div>
              <button className="modal-close" type="button" aria-label="关闭" onClick={() => setShowCreate(false)}><XMarkIcon aria-hidden="true" /></button>
            </header>
            <form className="admin-user-form" onSubmit={(event) => { event.preventDefault(); createUserMutation.mutate(); }}>
              <label><span>显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
              <label><span>用户名</span><input value={username} autoCapitalize="none" onChange={(event) => setUsername(event.target.value)} required /></label>
              <label><span>初始密码</span><input value={password} type="password" minLength={10} onChange={(event) => setPassword(event.target.value)} required /></label>
              <label><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value as "admin" | "member")}><option value="member">普通用户</option><option value="admin">管理员</option></select></label>
              <label><span>所属部门</span><select value={departmentId} onChange={(event) => setDepartmentId(event.target.value)}><option value="">未分部门</option>{departmentItems.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              {createUserMutation.error ? <ErrorState message={createUserMutation.error.message} /> : null}
              <div className="modal-actions"><button className="secondary-button" type="button" onClick={() => setShowCreate(false)}>取消</button><button className="product-primary-button" type="submit" disabled={createUserMutation.isPending || password.length < 10}>{createUserMutation.isPending ? "正在创建…" : "创建用户"}</button></div>
            </form>
          </section>
        </div>
      ) : null}

      {tab === "users" ? (
        <section className="admin-card admin-table-card">
          <div className="admin-section-heading"><div><span className="eyebrow">全部账号</span><h2>用户</h2><p>可访问知识数量按当前三种权限来源合并去重。</p></div><label className="admin-search-field"><span>搜索用户或部门</span><input value={search} placeholder="显示名称、用户名或部门" onChange={(event) => setSearch(event.target.value)} /></label></div>
          {users.isLoading ? <LoadingState label="正在加载用户…" /> : null}
          {users.error ? <ErrorState message={users.error.message} /> : null}
          {!users.isLoading && !users.error ? <UserRows users={users.data?.items ?? []} /> : null}
        </section>
      ) : null}

      {tab === "departments" ? (
        <section className="admin-card admin-table-card">
          <div className="admin-section-heading"><div><span className="eyebrow">部门</span><h2>部门</h2><p>部门成员自动继承部门默认可访问知识。</p></div><div className="admin-inline-form"><input value={departmentName} placeholder="新部门名称" onChange={(event) => setDepartmentName(event.target.value)} /><button className="secondary-button" type="button" disabled={!departmentName.trim() || createDepartmentMutation.isPending} onClick={() => createDepartmentMutation.mutate()}>{createDepartmentMutation.isPending ? "创建中…" : "新建部门"}</button></div></div>
          {departments.isLoading ? <LoadingState label="正在加载部门…" /> : null}
          {departments.error ? <ErrorState message={departments.error.message} /> : null}
          {!departments.isLoading && !departments.error ? <div className="admin-department-grid">
            {departmentItems.map((department) => <Link className="admin-department-card" key={department.id} to={`/admin/users/department/${department.id}`}><strong>{department.name}</strong><span>{department.user_count} 名用户 · 默认可访问 {department.knowledge_base_count} 个知识库</span><em>查看 →</em></Link>)}
            <Link className="admin-department-card" to="/admin/users/department/unassigned"><strong>未分部门</strong><span>{departments.data?.unassigned.user_count ?? 0} 名用户</span><em>查看 →</em></Link>
          </div> : null}
        </section>
      ) : null}

      {tab === "company-wide" ? (
        <section className="admin-card admin-access-card">
          <div className="admin-section-heading"><div><span className="eyebrow">默认范围</span><h2>全员可访问知识</h2><p>所有正常普通用户都可以使用这里配置的知识库。</p></div><strong>{companyIds.length} 个知识库</strong></div>
          {companyAccess.isLoading || knowledgeBases.isLoading ? <LoadingState label="正在加载全员知识…" /> : null}
          {companyAccess.error || knowledgeBases.error ? <ErrorState message={(companyAccess.error || knowledgeBases.error)?.message || "加载失败"} /> : null}
          {!companyAccess.isLoading && !knowledgeBases.isLoading && !companyAccess.error && !knowledgeBases.error ? <KnowledgeCheckboxes knowledgeBases={allKnowledgeBases} selected={companyIds} onChange={setCompanySelected} /> : null}
          <div className="admin-card-actions"><button className="product-primary-button" type="button" disabled={companyMutation.isPending} onClick={() => companyMutation.mutate()}>{companyMutation.isPending ? "保存中…" : "保存全员知识"}</button></div>
        </section>
      ) : null}
    </section>
  );
}

export function AdminDepartmentPage() {
  const { departmentId } = useParams();
  const queryClient = useQueryClient();
  const department = departmentId === "unassigned" ? null : departmentId || null;
  const users = useQuery({ queryKey: ["admin-department-users", department], queryFn: () => listDepartmentUsers(department) });
  const knowledgeBases = useQuery({ queryKey: ["admin-knowledge-bases"], queryFn: listAdminKnowledgeBases, enabled: Boolean(department) });
  const access = useQuery({ queryKey: ["admin-department-access", department], queryFn: () => getDepartmentKnowledgeAccess(department as string), enabled: Boolean(department) });
  const [selected, setSelected] = useState<string[] | null>(null);
  const selectedIds = selected ?? access.data?.knowledge_base_ids ?? [];
  const mutation = useMutation({
    mutationFn: () => setDepartmentKnowledgeAccess(department as string, selectedIds),
    onSuccess: (result) => {
      setSelected(result.knowledge_base_ids);
      void queryClient.invalidateQueries({ queryKey: ["admin-department-access", department] });
      void queryClient.invalidateQueries({ queryKey: ["admin-departments"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const baseItems = visibleKnowledgeBases(knowledgeBases.data ?? []);
  return (
    <section className="admin-page admin-users-page">
      <header className="admin-page-heading"><div><span className="eyebrow">用户管理 / 部门</span><h1>{users.data?.department?.name || "未分部门"}</h1><p>{users.data?.department?.user_count ?? users.data?.items.length ?? 0} 名用户 · 通过部门继承默认知识访问。</p></div><Link className="secondary-button" to="/admin/users?tab=departments">返回部门</Link></header>
      <section className="admin-card admin-table-card"><div className="admin-section-heading"><div><h2>部门用户</h2><p>调整用户所属部门请进入用户详情。</p></div></div>{users.isLoading ? <LoadingState label="正在加载部门用户…" /> : users.error ? <ErrorState message={users.error.message} /> : <UserRows users={users.data?.items ?? []} />}</section>
      {department ? <section className="admin-card admin-access-card"><div className="admin-section-heading"><div><span className="eyebrow">部门默认范围</span><h2>默认可访问知识</h2><p>部门用户会自动继承这些知识库。</p></div><strong>{selectedIds.length} 个知识库</strong></div>{access.isLoading || knowledgeBases.isLoading ? <LoadingState label="正在加载部门知识…" /> : access.error || knowledgeBases.error ? <ErrorState message={(access.error || knowledgeBases.error)?.message || "加载失败"} /> : <KnowledgeCheckboxes knowledgeBases={baseItems} selected={selectedIds} onChange={setSelected} />}<div className="admin-card-actions"><button className="product-primary-button" type="button" disabled={mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "保存中…" : "保存部门知识"}</button></div></section> : <section className="admin-card"><h2>未分部门</h2><p className="admin-muted">未分部门用户不继承部门知识，只能使用全员知识和个人额外授权。</p></section>}
    </section>
  );
}

function userExtraIds(access: UserKnowledgeAccess | undefined): string[] {
  return access?.personal_extra_access.map((item) => item.knowledge_base_id) ?? [];
}

export function AdminUserDetailPage() {
  const { userId } = useParams();
  const queryClient = useQueryClient();
  const userQuery = useQuery({ queryKey: ["admin-user", userId], queryFn: () => listAdminUsers({ page_size: 100 }), enabled: Boolean(userId) });
  const departments = useQuery({ queryKey: ["admin-departments"], queryFn: listAdminDepartments });
  const access = useQuery({ queryKey: ["admin-user-access", userId], queryFn: () => getUserKnowledgeAccess(userId as string), enabled: Boolean(userId) });
  const knowledgeBases = useQuery({ queryKey: ["admin-knowledge-bases"], queryFn: listAdminKnowledgeBases });
  const user = userQuery.data?.items.find((item) => item.id === userId);
  const [selected, setSelected] = useState<string[] | null>(null);
  const selectedIds = selected ?? userExtraIds(access.data);
  const accessMutation = useMutation({
    mutationFn: () => setUserKnowledgeAccess(userId as string, selectedIds),
    onSuccess: (result) => {
      setSelected(result.personal_extra_access.map((item) => item.knowledge_base_id));
      void queryClient.invalidateQueries({ queryKey: ["admin-user-access", userId] });
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const updateMutation = useMutation({
    mutationFn: (input: Parameters<typeof updateUser>[1]) => updateUser(userId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-user", userId] });
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-departments"] });
    },
  });
  if (userQuery.error || access.error || departments.error || knowledgeBases.error) return <section className="admin-page"><ErrorState message={(userQuery.error || access.error || departments.error || knowledgeBases.error)?.message || "加载用户详情失败"} /></section>;
  if (userQuery.isLoading || !user) return <section className="admin-page"><LoadingState label="正在加载用户…" /></section>;
  if (access.isLoading || departments.isLoading || knowledgeBases.isLoading || !access.data) return <section className="admin-page"><LoadingState label="正在加载权限详情…" /></section>;

  const baseItems = visibleKnowledgeBases(knowledgeBases.data ?? []);
  const inheritedIds = new Set(access.data.inherited_access.map((item) => item.knowledge_base_id));
  const extraItems = baseItems.filter((item) => !inheritedIds.has(item.id));
  const isAdmin = user.role === "admin" || access.data.is_admin;
  return (
    <section className="admin-page admin-users-page">
      <header className="admin-page-heading"><div><span className="eyebrow">用户管理 / 用户详情</span><h1>{user.display_name}</h1><p>@{user.username} · {isAdmin ? "管理员" : "普通用户"}</p></div><Link className="secondary-button" to="/admin/users?tab=users">返回用户</Link></header>
      <section className="admin-card admin-user-profile-grid"><div><span>用户名</span><strong>@{user.username}</strong></div><div><span>所属部门</span><select value={user.department_id || ""} onChange={(event) => updateMutation.mutate({ department_id: event.target.value || null })}><option value="">未分部门</option>{(departments.data?.items ?? []).map((item: Department) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></div><div><span>系统角色</span><strong>{isAdmin ? "管理员" : "普通用户"}</strong></div><div><span>账号状态</span><button className="secondary-button compact" type="button" onClick={() => updateMutation.mutate({ is_active: !user.is_active })}>{user.is_active ? "正常 · 停用" : "已停用 · 启用"}</button></div></section>

      <section className="admin-card admin-access-summary admin-user-access-stats">
        <div><span>继承访问</span><strong>{isAdmin ? "全量管理访问" : `${access.data.inherited_access.length} 个知识库`}</strong></div>
        <div><span>个人额外访问</span><strong>{isAdmin ? "不适用" : `${access.data.personal_extra_access.length} 个知识库`}</strong></div>
        <div><span>总可访问</span><strong>{access.data.effective_access_count} 个知识库</strong></div>
      </section>

      {isAdmin ? (
        <section className="admin-card admin-access-card">
          <div className="admin-section-heading"><div><span className="eyebrow">管理员访问</span><h2>全量管理访问</h2><p>管理员可以管理并查看全部知识库，不使用普通用户的个人额外授权语义。</p></div><strong>{access.data.effective_access_count} 个知识库</strong></div>
        </section>
      ) : (
        <>
          <section className="admin-card admin-access-card">
            <div className="admin-section-heading"><div><span className="eyebrow">继承访问 · 只读</span><h2>全员与部门继承</h2><p>这些访问来自全员或部门范围，不能在个人额外授权区域重复勾选。</p></div><strong>{access.data.inherited_access.length} 个知识库</strong></div>
            <InheritedAccessList items={access.data.inherited_access} />
          </section>
          <section className="admin-card admin-access-card">
            <div className="admin-section-heading"><div><span className="eyebrow">个人额外访问</span><h2>个人额外知识</h2><p>这里只保存超出当前继承范围的个人直接授权；继承范围变化后会自动重新计算。</p></div><strong>{selectedIds.length} 个知识库</strong></div>
            <p className="admin-muted">全员继承 {access.data.company_wide_access_count} 个 · 部门继承 {access.data.department_access_count} 个 · 总可访问 {access.data.effective_access_count} 个</p>
            <KnowledgeCheckboxes knowledgeBases={extraItems} selected={selectedIds} onChange={setSelected} />
            <div className="admin-card-actions"><button className="product-primary-button" type="button" disabled={accessMutation.isPending} onClick={() => accessMutation.mutate()}>{accessMutation.isPending ? "保存中…" : "保存个人额外知识"}</button></div>
          </section>
        </>
      )}
    </section>
  );
}
