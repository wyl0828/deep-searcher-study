const MAX_PDF_BYTES = 20 * 1024 * 1024;

async function readResponse(response) {
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }

  if (!response.ok) {
    throw new Error(payload.detail || "本地服务请求失败，请稍后重试");
  }
  return payload;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  return readResponse(response);
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取所选 PDF 文件"));
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.readAsDataURL(file);
  });
}

export async function getHealth() {
  return requestJson("/api/health");
}

export async function ingestPdf(file, collectionName) {
  if (!file || !file.name.toLowerCase().endsWith(".pdf") || file.type !== "application/pdf") {
    throw new Error("仅支持 PDF 文件");
  }
  if (file.size === 0) {
    throw new Error("PDF 文件不能为空");
  }
  if (file.size > MAX_PDF_BYTES) {
    throw new Error("PDF 文件不能超过 20 MiB");
  }

  const contentBase64 = await fileToBase64(file);
  return requestJson("/api/ingest", {
    method: "POST",
    body: JSON.stringify({
      filename: file.name,
      content_base64: contentBase64,
      collection_name: collectionName,
    }),
  });
}

export async function queryDeepSearcher(question, maxIter) {
  const normalizedQuestion = question.trim();
  if (!normalizedQuestion) {
    throw new Error("请输入问题");
  }

  const payload = await requestJson("/api/query", {
    method: "POST",
    body: JSON.stringify({ question: normalizedQuestion, max_iter: maxIter }),
  });
  return {
    answer: payload.result,
    totalTokens: payload.consume_token,
    latencyMs: payload.latency_ms,
  };
}
