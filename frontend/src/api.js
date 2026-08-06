const MAX_PDF_BYTES = 20 * 1024 * 1024;

async function readResponse(response) {
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }

  if (!response.ok) {
    throw new Error(payload.error?.message || payload.detail || "本地服务请求失败，请稍后重试");
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

export async function getHealth({ deep = false } = {}) {
  return requestJson(deep ? "/api/health/diagnostics" : "/api/health", {
    method: deep ? "POST" : "GET",
  });
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

  const formData = new FormData();
  formData.append("file", file);
  formData.append("collection_name", collectionName);
  const response = await fetch("/api/ingest", {
    method: "POST",
    body: formData,
  });
  return readResponse(response);
}

export async function queryDeepSearcher(
  question,
  maxIter,
  collectionName,
  useWebSearch = false,
) {
  const normalizedQuestion = question.trim();
  if (!normalizedQuestion) {
    throw new Error("请输入问题");
  }

  const payload = await requestJson("/api/query", {
    method: "POST",
    body: JSON.stringify({
      question: normalizedQuestion,
      max_iter: maxIter,
      collection_name: collectionName,
      use_web_search: useWebSearch,
    }),
  });
  return {
    answer: payload.result,
    totalTokens: payload.consume_token,
    latencyMs: payload.latency_ms,
    trace: payload.trace || null,
  };
}
