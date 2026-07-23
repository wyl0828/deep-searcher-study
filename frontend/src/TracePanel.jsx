import {
  CheckCircleIcon,
  ChevronDownIcon,
  CircleStackIcon,
  DocumentTextIcon,
  SparklesIcon,
  TrashIcon,
} from "@heroicons/react/24/outline";
import { useEffect, useState } from "react";

function reflectionText(value) {
  if (value === true) return "信息已足够";
  if (value === false) return "继续检索";
  return "未执行反思";
}

function scoreText(score) {
  return typeof score === "number" ? score.toFixed(4) : "—";
}

function TraceEmpty({ state }) {
  if (state === "loading") {
    return <p className="trace-empty">正在执行查询，完成后会展示每轮检索过程…</p>;
  }
  if (state === "error") {
    return <p className="trace-empty trace-empty--error">查询失败，本次没有可展示的查询过程。</p>;
  }
  return <p className="trace-empty">运行查询后，这里会按轮次展示路由、检索和反思结果。</p>;
}

export function TracePanel({ trace, logs = [], onClearLogs, state = "idle" }) {
  const [activeTab, setActiveTab] = useState("trace");
  const [expandedRounds, setExpandedRounds] = useState(new Set());

  useEffect(() => {
    const firstIndex = trace?.iterations?.[0]?.index;
    setExpandedRounds(firstIndex == null ? new Set() : new Set([firstIndex]));
    if (trace) setActiveTab("trace");
  }, [trace]);

  function toggleRound(index) {
    setExpandedRounds((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  return (
    <article className="event-panel trace-panel">
      <div className="trace-tabs" role="tablist" aria-label="查询详情">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "trace"}
          className={activeTab === "trace" ? "active" : ""}
          onClick={() => setActiveTab("trace")}
        >
          查询过程
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "logs"}
          className={activeTab === "logs" ? "active" : ""}
          onClick={() => setActiveTab("logs")}
        >
          系统日志
        </button>
        {activeTab === "logs" ? (
          <button
            className="trace-clear"
            type="button"
            onClick={onClearLogs}
            disabled={!logs.length}
          >
            <TrashIcon aria-hidden="true" />
            清空
          </button>
        ) : null}
      </div>

      {activeTab === "trace" ? (
        <div className="trace-content" role="tabpanel">
          {!trace ? (
            <TraceEmpty state={state} />
          ) : (
            <>
              <div className="trace-summary">
                <div>
                  <span>执行 Agent</span>
                  <strong>{trace.agent || "未识别"}</strong>
                </div>
                <div>
                  <span>实际迭代</span>
                  <strong>{trace.summary?.iteration_count ?? trace.iterations?.length ?? 0} 轮</strong>
                </div>
                <div>
                  <span>采用文档</span>
                  <strong>{trace.summary?.supported_document_count ?? 0} 条</strong>
                </div>
                <div>
                  <span>总 Token</span>
                  <strong>{trace.summary?.total_tokens ?? "—"}</strong>
                </div>
              </div>

              <div className="trace-rounds">
                {(trace.iterations || []).length ? (
                  trace.iterations.map((iteration) => {
                    const expanded = expandedRounds.has(iteration.index);
                    const visibleCount = iteration.retrieved_documents?.length || 0;
                    return (
                      <section className="trace-round" key={iteration.index}>
                        <button
                          type="button"
                          className="trace-round-toggle"
                          aria-expanded={expanded}
                          onClick={() => toggleRound(iteration.index)}
                        >
                          <span className="trace-round-number">{iteration.index}</span>
                          <span>
                            <strong>第 {iteration.index} 轮</strong>
                            <small>{iteration.subquery || "未生成子查询"}</small>
                          </span>
                          <span className={`reflection reflection--${iteration.has_enough_information}`}>
                            {reflectionText(iteration.has_enough_information)}
                          </span>
                          <ChevronDownIcon className={expanded ? "expanded" : ""} aria-hidden="true" />
                        </button>

                        {expanded ? (
                          <div className="trace-round-body">
                            <div className="trace-detail-row">
                              <SparklesIcon aria-hidden="true" />
                              <div>
                                <span>本轮子查询</span>
                                <p>{iteration.subquery || "未生成"}</p>
                              </div>
                            </div>
                            <div className="trace-detail-row">
                              <CircleStackIcon aria-hidden="true" />
                              <div>
                                <span>检索集合</span>
                                <p className="collection-chips">
                                  {(iteration.collections || []).length
                                    ? iteration.collections.map((name) => <b key={name}>{name}</b>)
                                    : "未返回"}
                                </p>
                              </div>
                            </div>

                            <div className="trace-documents">
                              <div className="trace-documents-heading">
                                <span>检索文档</span>
                                <small>
                                  命中 {iteration.retrieved_count ?? 0} 条，仅展示前 {visibleCount} 条
                                </small>
                              </div>
                              {visibleCount ? (
                                iteration.retrieved_documents.map((document, documentIndex) => (
                                  <details className="trace-document" key={`${iteration.index}-${documentIndex}`} open={documentIndex === 0}>
                                    <summary>
                                      <DocumentTextIcon aria-hidden="true" />
                                      <span>{document.reference || `文档片段 ${documentIndex + 1}`}</span>
                                      <small>相似度 {scoreText(document.score)}</small>
                                      {document.supported ? (
                                        <em><CheckCircleIcon aria-hidden="true" />已采用</em>
                                      ) : null}
                                    </summary>
                                    <p>{document.text || "无可展示文本"}</p>
                                  </details>
                                ))
                              ) : (
                                <p className="trace-no-docs">本轮没有返回可展示的文档片段。</p>
                              )}
                            </div>

                            <div className="trace-answer-preview">
                              <span>阶段回答</span>
                              <p>{iteration.intermediate_answer || "未返回阶段回答"}</p>
                            </div>
                            <div className="trace-round-footer">
                              <span>反思：{reflectionText(iteration.has_enough_information)}</span>
                              <span>本轮 Token {iteration.token_usage?.total ?? "—"}</span>
                            </div>
                          </div>
                        ) : null}
                      </section>
                    );
                  })
                ) : (
                  <p className="trace-empty">后端已返回查询信息，但没有产生迭代记录。</p>
                )}
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="event-list" role="tabpanel" aria-live="polite">
          {logs.length ? (
            logs.map((log) => (
              <div className={`event-item event-item--${log.tone}`} key={log.id}>
                <time>{log.time}</time>
                <span>{log.message}</span>
              </div>
            ))
          ) : (
            <p className="empty-log">入库或查询后，这里会记录可确认的系统事件。</p>
          )}
        </div>
      )}
    </article>
  );
}
