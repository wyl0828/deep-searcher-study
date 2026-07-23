# 常见问题

## 🔍 常见故障与解决方法

---

### 💬 问题 1：为什么无法解析 LLM 的输出格式？应该选择哪个模型？

<div class="faq-answer">
<p><strong>解决方法：</strong>参数量较小的模型可能无法严格遵循提示词，也容易生成不符合预期格式的回答。建议使用推理能力更强的模型，例如：</p>

<ul>
  <li>DeepSeek-R1 671B</li>
  <li>OpenAI o 系列模型</li>
  <li>Claude 3.7 Sonnet</li>
</ul>

<p>这些模型通常具有更强的推理和指令遵循能力，更容易输出正确格式。</p>
</div>

---

### 🌐 问题 2：出现“无法连接 https://huggingface.co”错误

<div class="faq-answer">
<p><strong>典型错误：</strong></p>
<div class="error-message">
OSError: We couldn't connect to 'https://huggingface.co' to load this file, couldn't find it in the cached files and it looks like GPTCache/paraphrase-albert-small-v2 is not the path to a directory containing a file named config.json.
Checkout your internet connection or see how to run the library in offline mode at 'https://huggingface.co/docs/transformers/installation#offline-mode'.
</div>

<p><strong>解决方法：</strong>这通常是无法正常访问 Hugging Face 导致的，可以尝试以下方式。</p>

<details>
<summary><strong>网络问题：使用镜像地址</strong></summary>

```bash
export HF_ENDPOINT=https://hf-mirror.com
```
</details>

<details>
<summary><strong>权限问题：设置个人 Token</strong></summary>

```bash
export HUGGING_FACE_HUB_TOKEN=xxxx
```
</details>
</div>

---

### 📓 问题 3：DeepSearcher 无法在 Jupyter Notebook 中运行

<div class="faq-answer">
<p><strong>解决方法：</strong>这通常是 Jupyter Notebook 中的 asyncio 事件循环冲突。安装 <code>nest_asyncio</code>，并在 Notebook 开头加入初始化代码。</p>

<div class="code-steps">
<p><strong>第 1 步：</strong>安装依赖</p>

```bash
pip install nest_asyncio
```

<p><strong>第 2 步：</strong>在 Notebook 开头加入</p>

```python
import nest_asyncio
nest_asyncio.apply()
```
</div>
</div>
