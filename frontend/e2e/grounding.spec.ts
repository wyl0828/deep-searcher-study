import { expect, test, type Page } from "@playwright/test";

const conversationPath = "/chat/conv_e2e_grounding";
const evidenceText =
  "这是传给最终模型并持久化的同一份较宽证据快照，包含被引用事实。原始回答正文应完整显示。";

function collectPageErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("用户名").fill("e2e-user");
  await page.getByLabel("密码").fill("e2e-secure-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登录学习工作台" })).toBeHidden();
}

test("保留原答案和代码块，并把 Claim 作为附加核验展示", async ({ page }) => {
  const errors = collectPageErrors(page);
  await login(page);
  await page.goto(conversationPath);

  const answer = page.locator("article.assistant-message");
  const originalMarkdown = answer.locator(".markdown-answer").first();
  const code = originalMarkdown.locator("pre code");
  await expect(originalMarkdown).toContainText("原始回答正文应完整显示。");
  await expect(originalMarkdown).toContainText("第二条结论需要单独核验。");
  await expect(originalMarkdown).not.toContainText("[E1]");
  await expect(originalMarkdown).not.toContainText("[E9]");
  await expect(code).toHaveText("print('preserved')");

  const codeStyle = await code.evaluate((element) => {
    const style = getComputedStyle(element);
    const parentStyle = getComputedStyle(element.parentElement as HTMLElement);
    return {
      background: style.backgroundColor,
      color: style.color,
      parentColor: parentStyle.color,
    };
  });
  expect(codeStyle.background).toBe("rgba(0, 0, 0, 0)");
  expect(codeStyle.color).toBe(codeStyle.parentColor);

  const claimDetails = answer.locator("details.claim-grounding");
  await expect(claimDetails).not.toHaveAttribute("open", "");
  await claimDetails.locator("summary").click();
  await expect(claimDetails).toHaveAttribute("open", "");
  await expect(claimDetails.getByText("已有依据", { exact: true })).toBeVisible();
  await expect(claimDetails.getByText("引用无效", { exact: true })).toBeVisible();

  await claimDetails
    .getByRole("button", {
      name: "查看声明 1 的引用 1：grounding-evidence.pdf，第 2 页",
    })
    .click();
  await expect(page.getByTitle("声明文字在证据中的精确位置")).toHaveText(
    "原始回答正文应完整显示。",
  );
  await expect(page.getByText(evidenceText, { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.locator("article.assistant-message pre code")).toHaveText(
    "print('preserved')",
  );
  await expect(page.locator("details.claim-grounding")).not.toHaveAttribute("open", "");
  expect(errors).toEqual([]);
});

test("移动端引用抽屉可打开、关闭并恢复焦点，页面没有横向溢出", async ({ page }) => {
  const errors = collectPageErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto(conversationPath);

  const citationTrigger = page.getByRole("button", {
    name: "查看引用 1：grounding-evidence.pdf，第 2 页",
  });
  await expect(citationTrigger).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    ),
  ).toBe(true);

  await citationTrigger.click();
  const drawer = page.getByRole("dialog", { name: "引用来源" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveAttribute("aria-modal", "true");
  await expect(drawer.getByText(evidenceText, { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "关闭引用来源" }).click();
  await expect(drawer).toBeHidden();
  await expect(citationTrigger).toBeFocused();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录学习工作台" })).toBeVisible();
  expect(errors).toEqual([]);
});
