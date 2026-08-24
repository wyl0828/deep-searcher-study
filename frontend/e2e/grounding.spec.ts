import { expect, test, type Page } from "@playwright/test";

const conversationPath = "/chat/conv_e2e_grounding";

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

test("保留原答案和代码块，并把来源收缩为轻量入口", async ({ page }) => {
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

  await expect(answer.locator("details.claim-grounding")).toHaveCount(0);
  await expect(answer.getByText("查看回答依据", { exact: true })).toHaveCount(0);
  await expect(answer.getByRole("button", { name: /引用来源（\d+）/ })).toBeVisible();

  await answer
    .getByRole("button", {
      name: "查看引用 1：grounding-evidence.pdf，第 2 页",
    })
    .click();
  await expect(page.locator("#citation-drawer")).toBeVisible();
  await expect(page.locator("#citation-drawer")).toContainText("这是传给最终模型");

  await page.reload();
  await expect(page.locator("article.assistant-message pre code")).toHaveText(
    "print('preserved')",
  );
  await expect(page.locator("details.claim-grounding")).toHaveCount(0);
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
  await expect(drawer).toContainText("这是传给最终模型");
  await drawer.getByRole("button", { name: "关闭引用来源" }).click();
  await expect(drawer).toBeHidden();
  await expect(citationTrigger).toBeFocused();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录学习工作台" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("图片引用受控预览，来源默认显示前五条并可在二级层查看原文", async ({ page }) => {
  const errors = collectPageErrors(page);
  await login(page);
  await page.goto("/chat/conv_e2e_media");

  const answer = page.locator("article.assistant-message");
  const previewImage = answer.locator(".markdown-image-preview img");
  await expect(previewImage).toBeVisible();
  const imageBox = await previewImage.boundingBox();
  expect(imageBox?.height || 0).toBeLessThanOrEqual(180);
  await expect(answer.getByRole("button", { name: "查看图片" })).toBeVisible();

  await answer.getByRole("button", { name: "查看图片" }).click();
  const imageDialog = page.getByRole("dialog", { name: "测试原文图片" });
  await expect(imageDialog).toBeVisible();
  await imageDialog.getByRole("button", { name: "关闭原文预览" }).click();
  await expect(imageDialog).toBeHidden();

  const sourceTrigger = answer.getByRole("button", { name: "引用来源（27）" });
  await sourceTrigger.click();
  const drawer = page.locator("#citation-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.locator(".citation-card")).toHaveCount(5);
  await expect(drawer.getByRole("button", { name: /查看更多/ })).toBeVisible();

  await drawer
    .getByRole("button", { name: /查看 media-evidence\.pdf 第 1 页的原文/ })
    .click();
  const documentDialog = page.getByRole("dialog", { name: /media-evidence\.pdf/ });
  await expect(documentDialog).toBeVisible();
  await expect(documentDialog.locator("iframe")).toBeVisible();
  await documentDialog.getByRole("button", { name: "关闭原文预览" }).click();
  await expect(documentDialog).toBeHidden();

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
