import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { ErrorState } from "../components/states";
import { loginWorkspace, setupWorkspace } from "../product-api";

export function AuthScreen({ setupRequired }: { setupRequired: boolean }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      setupRequired
        ? setupWorkspace({
            username,
            password,
            display_name: displayName,
          })
        : loginWorkspace({ username, password }),
    onSuccess: (user) => {
      queryClient.setQueryData(["auth-status"], {
        setup_required: false,
        authenticated: true,
        user,
      });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <img src="/deepsearcher-logo.png" alt="DeepSearcher" />
        <div className="auth-heading">
          <span>{setupRequired ? "首次使用" : "欢迎回来"}</span>
          <h1 id="auth-title">
            {setupRequired ? "创建工作台管理员" : "登录学习工作台"}
          </h1>
          <p>
            {setupRequired
        ? "首位用户将成为管理员，负责配置企业知识和员工访问范围。"
        : "登录后即可向管理员配置的企业知识提问。"}
          </p>
        </div>
        <form onSubmit={submit}>
          {setupRequired ? (
            <label>
              <span>显示名称</span>
              <input
                autoComplete="name"
                maxLength={50}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="例如：小林"
                required
              />
            </label>
          ) : null}
          <label>
            <span>用户名</span>
            <input
              autoCapitalize="none"
              autoComplete="username"
              maxLength={32}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="3–32 位字母、数字或 ._-"
              required
            />
          </label>
          <label>
            <span>密码</span>
            <input
              autoComplete={setupRequired ? "new-password" : "current-password"}
              minLength={setupRequired ? 10 : 1}
              maxLength={128}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={setupRequired ? "至少 10 个字符" : "输入密码"}
              required
            />
          </label>
          {mutation.error ? <ErrorState message={mutation.error.message} /> : null}
          <button
            className="product-primary-button auth-submit"
            type="submit"
            disabled={
              mutation.isPending ||
              !username.trim() ||
              !password ||
              (setupRequired && !displayName.trim())
            }
          >
            {mutation.isPending
              ? "正在提交…"
              : setupRequired
                ? "创建管理员并进入"
                : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
