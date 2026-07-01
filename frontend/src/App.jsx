import {
  ArrowRightIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
  CheckCircleIcon,
  CircleStackIcon,
  ClipboardDocumentIcon,
  CloudArrowUpIcon,
  CpuChipIcon,
  CubeTransparentIcon,
  DocumentTextIcon,
  ExclamationCircleIcon,
  InformationCircleIcon,
  MagnifyingGlassIcon,
  PaperAirplaneIcon,
  PlayIcon,
  ShareIcon,
  SparklesIcon,
  TrashIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getHealth, ingestPdf, queryDeepSearcher } from "./api";

const INGESTION_STEPS = [
  { marker: "1", title: "PDF 解析", description: "提取文档文本", icon: DocumentTextIcon },
  { marker: "2", title: "文本切分", description: "按规则切分块", icon: ShareIcon },
  { marker: "3", title: "Embedding", description: "生成向量表示", icon: CubeTransparentIcon },
  { marker: "4", title: "写入 Milvus", description: "向量存储", icon: CircleStackIcon },
];

const QUERY_STEPS = [
  { marker: "A", title: "用户问题", description: "输入自然语言问题", icon: ChatBubbleLeftRightIcon },
  { marker: "B", title: "查询路由", description: "生成子查询", icon: ShareIcon },
  { marker: "C", title: "向量检索", description: "Milvus 检索相关片段", icon: MagnifyingGlassIcon },
  { marker: "D", title: "Chain of RAG", description: "构建上下文并生成回答", icon: SparklesIcon },
  { marker: "E", title: "最终回答", description: "返回答案给用户", icon: PaperAirplaneIcon },
];

const EMPTY_HEALTH = {
  services: {
    fastapi: { state: "checking" },
    milvus: { state: "checking" },
    llm: { state: "checking" },
    embedding: { state: "checking" },
  },
  config: {
    collection: "deepsearcher",
    llm_model: "读取中…",
    embedding_model: "读取中…",
  },
};

function now() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function stateText(state) {
  if (state === "loading") return "后台处理中";
  if (state === "success") return "流程完成（汇总）";
  if (state === "error") return "执行失败";
  return "等待操作";
}

function FlowNode({ step, state, sample }) {
  const Icon = step.icon;
  const statusClass = state === "success" ? "success" : state === "error" ? "error" : state;
  return (
    <div className={`flow-node flow-node--${statusClass}`}>
      <span className="flow-marker">{step.marker}</span>
      <Icon className="flow-icon" aria-hidden="true" />
      <strong>{step.title}</strong>
      <span>{step.description}</span>
      {sample ? <small title={sample}>{sample}</small> : null}
      <div className={`node-status node-status--${statusClass}`}>
        {state === "success" ? <CheckCircleIcon aria-hidden="true" /> : null}
        {state === "error" ? <ExclamationCircleIcon aria-hidden="true" /> : null}
        <span>{stateText(state)}</span>
      </div>
    </div>
  );
}

function FlowLane({ steps, state, question }) {
  return (
    <div className={`flow-lane flow-lane--${steps.length}`}>
      {steps.map((step, index) => (
        <div className="flow-segment" key={step.marker}>
          <FlowNode
            step={step}
            state={state}
            sample={index === 0 && question ? question : undefined}
          />
          {index < steps.length - 1 ? (
            <ArrowRightIcon className="flow-arrow" aria-hidden="true" />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function ServiceItem({ icon: Icon, name, state }) {
  const copy = {
    online: "正常",
    configured: "已配置",
    offline: "离线",
    checking: "检查中",
  }[state];
  return (
    <div className="service-item">
      <Icon aria-hidden="true" />
      <span>{name}</span>
      <span className={`service-state service-state--${state}`}>
        <i aria-hidden="true" />
        {copy}
      </span>
    </div>
  );
}

function MetricRow({ label, value }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function App() {
  const [health, setHealth] = useState(EMPTY_HEALTH);
  const [healthError, setHealthError] = useState("");
  const [file, setFile] = useState(null);
  const [collection, setCollection] = useState("deepsearcher");
  const [ingestState, setIngestState] = useState("idle");
  const [ingestMessage, setIngestMessage] = useState("选择 PDF 后开始入库");
  const [question, setQuestion] = useState("");
  const [maxIter, setMaxIter] = useState(3);
  const [queryState, setQueryState] = useState("idle");
  const [answer, setAnswer] = useState("");
  const [latencyMs, setLatencyMs] = useState(null);
  const [totalTokens, setTotalTokens] = useState(null);
  const [logs, setLogs] = useState([]);
  const [copied, setCopied] = useState(false);
  const fileInputRef = useRef(null);

  const addLog = useCallback((message, tone = "info") => {
    setLogs((current) => [...current, { id: crypto.randomUUID(), time: now(), message, tone }]);
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      const payload = await getHealth();
      setHealth(payload);
      setCollection((current) =>
        current === "deepsearcher" ? payload.config?.collection || current : current,
      );
      setHealthError("");
    } catch (error) {
      setHealthError(error.message);
      setHealth((current) => ({
        ...current,
        services: Object.fromEntries(
          Object.keys(current.services).map((key) => [key, { state: "offline" }]),
        ),
      }));
    }
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  const services = useMemo(
    () => [
      { key: "fastapi", name: "FastAPI 服务", icon: BoltIcon },
      { key: "milvus", name: "Milvus 向量库", icon: CircleStackIcon },
      { key: "llm", name: "LLM 大模型", icon: ChatBubbleLeftRightIcon },
      { key: "embedding", name: "Embedding 模型", icon: CpuChipIcon },
    ],
    [],
  );

  async function handleIngest(event) {
    event.preventDefault();
    setIngestState("loading");
    setIngestMessage("后台正在处理；当前 API 不提供分阶段进度");
    addLog(`开始入库：${file?.name || "尚未选择文件"}`);
    try {
      const payload = await ingestPdf(file, collection);
      setIngestState("success");
      setIngestMessage(payload.message || "入库请求成功");
      addLog(`入库完成：Collection ${payload.collection_name || collection}`, "success");
      await refreshHealth();
    } catch (error) {
      setIngestState("error");
      setIngestMessage(error.message);
      addLog(`入库失败：${error.message}`, "error");
    }
  }

  async function handleQuery(event) {
    event.preventDefault();
    setQueryState("loading");
    setAnswer("");
    setLatencyMs(null);
    setTotalTokens(null);
    addLog(`开始查询：${question.trim() || "问题为空"}`);
    try {
      const result = await queryDeepSearcher(question, maxIter);
      setAnswer(result.answer);
      setLatencyMs(result.latencyMs);
      setTotalTokens(result.totalTokens);
      setQueryState("success");
      addLog(`查询完成：耗时 ${(result.latencyMs / 1000).toFixed(2)} 秒`, "success");
      addLog(`后端返回总 Token：${result.totalTokens ?? "未提供"}`, "success");
    } catch (error) {
      setQueryState("error");
      setAnswer(error.message);
      addLog(`查询失败：${error.message}`, "error");
    }
  }

  async function copyAnswer() {
    if (!answer) return;
    await navigator.clipboard.writeText(answer);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <img src="/deepsearcher-logo.png" alt="DeepSearcher" />
        <h1>学习控制台</h1>
        <span className="design-badge">方案 2 · 双泳道画布</span>
      </header>

      <div className="workspace">
        <main className="main-column">
          <section className="workflow-section workflow-section--ingest">
            <div className="section-heading">
              <div>
                <h2>离线入库 <span>（Ingestion）</span></h2>
                <p>将文档解析、切分并向量化后写入 Milvus</p>
              </div>
              <span className={`overall-state overall-state--${ingestState}`}>
                {stateText(ingestState)}
              </span>
            </div>
            <FlowLane steps={INGESTION_STEPS} state={ingestState} />

            <form className="control-row ingestion-controls" onSubmit={handleIngest}>
              <div className="field-group field-group--file">
                <label>上传 PDF</label>
                <input
                  ref={fileInputRef}
                  className="visually-hidden"
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={(event) => {
                    setFile(event.target.files?.[0] || null);
                    setIngestState("idle");
                    setIngestMessage("文件已选择，等待开始入库");
                  }}
                />
                <button
                  className="file-picker"
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <DocumentTextIcon aria-hidden="true" />
                  <span>{file?.name || "选择一份 PDF 文档"}</span>
                  {file ? <small>{(file.size / 1024 / 1024).toFixed(2)} MiB</small> : null}
                  {file ? (
                    <XMarkIcon
                      className="remove-file"
                      aria-label="移除文件"
                      onClick={(event) => {
                        event.stopPropagation();
                        setFile(null);
                        if (fileInputRef.current) fileInputRef.current.value = "";
                      }}
                    />
                  ) : (
                    <CloudArrowUpIcon className="upload-hint" aria-hidden="true" />
                  )}
                </button>
              </div>
              <div className="field-group field-group--collection">
                <label htmlFor="collection">集合名称</label>
                <input
                  id="collection"
                  value={collection}
                  onChange={(event) => setCollection(event.target.value)}
                  spellCheck="false"
                />
              </div>
              <button className="primary-button" disabled={ingestState === "loading"} type="submit">
                <PlayIcon aria-hidden="true" />
                {ingestState === "loading" ? "正在入库" : "开始入库"}
              </button>
              <div className={`operation-summary operation-summary--${ingestState}`}>
                {ingestState === "success" ? <CheckCircleIcon aria-hidden="true" /> : null}
                {ingestState === "error" ? <ExclamationCircleIcon aria-hidden="true" /> : null}
                <div>
                  <strong>{ingestMessage}</strong>
                  <span>页数、切片与向量数量：API 未提供</span>
                </div>
              </div>
            </form>
          </section>

          <section className="workflow-section workflow-section--query">
            <div className="section-heading">
              <div>
                <h2>在线问答 <span>（Query）</span></h2>
                <p>基于 RAG 流程回答问题</p>
              </div>
              <span className={`overall-state overall-state--${queryState}`}>
                {stateText(queryState)}
              </span>
            </div>
            <FlowLane
              steps={QUERY_STEPS}
              state={queryState}
              question={question.trim() ? question.trim().slice(0, 32) : ""}
            />

            <form className="control-row query-controls" onSubmit={handleQuery}>
              <div className="field-group field-group--question">
                <label htmlFor="question">问题</label>
                <input
                  id="question"
                  aria-label="问题"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="例如：Project Aurora 的负责人和上线日期是什么？"
                />
              </div>
              <div className="field-group field-group--iterations">
                <label htmlFor="max-iter">max_iter（最大迭代次数）</label>
                <input
                  id="max-iter"
                  type="number"
                  min="1"
                  max="10"
                  value={maxIter}
                  onChange={(event) => setMaxIter(Number(event.target.value))}
                />
              </div>
              <button className="primary-button query-button" disabled={queryState === "loading"} type="submit">
                <PlayIcon aria-hidden="true" />
                {queryState === "loading" ? "查询中" : "运行查询"}
              </button>
            </form>
          </section>

          <section className="result-area">
            <article className="answer-panel">
              <div className="panel-heading">
                <h2>最终回答</h2>
                <button type="button" onClick={copyAnswer} disabled={!answer}>
                  <ClipboardDocumentIcon aria-hidden="true" />
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
              <div className={`answer-content ${answer ? "answer-content--ready" : ""}`}>
                {answer || "运行查询后，最终答案会显示在这里。"}
              </div>
              <div className="answer-metrics">
                <span>延迟 {latencyMs == null ? "—" : `${(latencyMs / 1000).toFixed(2)} s`}</span>
                <span>提示词 Tokens 未提供</span>
                <span>完成 Tokens 未提供</span>
                <span>总 Tokens {totalTokens ?? "—"}</span>
              </div>
            </article>

            <article className="event-panel">
              <div className="panel-heading">
                <h2>事件日志 <span>（仅显示可观测阶段）</span></h2>
                <button type="button" onClick={() => setLogs([])} disabled={!logs.length}>
                  <TrashIcon aria-hidden="true" />
                  清空
                </button>
              </div>
              <div className="event-list" aria-live="polite">
                {logs.length ? (
                  logs.map((log) => (
                    <div className={`event-item event-item--${log.tone}`} key={log.id}>
                      <time>{log.time}</time>
                      <span>{log.message}</span>
                    </div>
                  ))
                ) : (
                  <p className="empty-log">入库或查询后，这里会记录可确认的事件。</p>
                )}
              </div>
            </article>
          </section>
        </main>

        <aside className="status-rail">
          <section>
            <div className="rail-heading">
              <h2>服务状态</h2>
              <button type="button" onClick={refreshHealth}>刷新</button>
            </div>
            <div className="service-list">
              {services.map((service) => (
                <ServiceItem
                  key={service.key}
                  icon={service.icon}
                  name={service.name}
                  state={health.services?.[service.key]?.state || "offline"}
                />
              ))}
            </div>
            {healthError ? <p className="rail-error">{healthError}</p> : null}
          </section>

          <section>
            <h2>当前配置</h2>
            <div className="metric-list">
              <MetricRow label="集合（Collection）" value={collection || "—"} />
              <MetricRow label="嵌入模型（Embedding）" value={health.config?.embedding_model || "—"} />
              <MetricRow label="大模型（LLM）" value={health.config?.llm_model || "—"} />
              <MetricRow label="max_iter" value={maxIter} />
            </div>
          </section>

          <section>
            <h2>本次查询统计</h2>
            <div className="metric-list">
              <MetricRow label="延迟（Latency）" value={latencyMs == null ? "—" : `${(latencyMs / 1000).toFixed(2)} s`} />
              <MetricRow label="提示词 Tokens" value="未提供" />
              <MetricRow label="完成 Tokens" value="未提供" />
              <MetricRow label="总 Tokens" value={totalTokens ?? "—"} />
            </div>
          </section>

          <div className="trace-note">
            <InformationCircleIcon aria-hidden="true" />
            <p>右侧为本次查询的最终统计结果，不包含内部推理细节或中间状态。</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
