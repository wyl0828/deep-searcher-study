import { ArrowPathIcon, CheckCircleIcon, XMarkIcon } from "@heroicons/react/24/outline";

import { ProductApiError, type QueryStageEvent } from "../../product-api";

export const SUGGESTED_QUESTIONS = [
  {
    title: "总结资料",
    description: "快速整理文档中的核心内容与关键要点...",
    prompt: "总结这些资料的核心内容",
    icon: "document",
  },
  {
    title: "提取结论",
    description: "从资料中找到关键事实、论据与结论...",
    prompt: "最重要的三个结论是什么？",
    icon: "insights",
  },
  {
    title: "行动建议",
    description: "根据已有资料形成清晰的可执行下一步建议...",
    prompt: "根据已有资料下一步怎么做？",
    icon: "actions",
  },
];

export function displayAnswerContent(content: string) {
  let insideCodeFence = false;
  return content
    .split("\n")
    .map((line) => {
      if (line.trimStart().startsWith("```")) {
        insideCodeFence = !insideCodeFence;
        return line;
      }
      if (insideCodeFence) return line;
      return line
        .replace(/\[\s*(?:E[1-9]\d{0,2}|CONFLICT\s*:\s*E[1-9]\d{0,2}(?:\s*,\s*E[1-9]\d{0,2})+)\s*\]/gi, "")
        .replace(/[ \t]+([。！？!?.,])/g, "$1");
    })
    .join("\n");
}

function stageLabel(stage: QueryStageEvent) {
  switch (stage.event) {
    case "started": return stage.data.stage === "chat_started" ? "已开始生成回答" : "已开始处理问题";
    case "contextualization": return stage.data.depends_on_history ? `已结合 ${stage.data.history_turn_count} 条历史消息理解追问` : "当前问题可独立检索";
    case "routing": return `已选择 ${stage.data.agent} 检索流程`;
    case "iteration": return `正在进行第 ${stage.data.iteration} 轮检索`;
    case "retrieval": return `已找到 ${stage.data.retrieved_count} 个候选片段`;
    case "web_search": return stage.data.status === "disabled" ? "联网搜索未配置，继续使用企业知识" : `联网搜索完成，获得 ${stage.data.result_count} 个网页片段`;
    case "support": return `其中 ${stage.data.supported_count} 个片段通过证据核验`;
    case "reflection": return stage.data.has_enough_information ? "证据检查完成，正在组织回答" : "已完成本轮证据检查";
  }
}

export function QueryProgress({ stages, onStop }: { stages: QueryStageEvent[]; onStop: () => void }) {
  const visibleStages = stages.slice(-5);
  const chatStarted = stages.some(
    (stage) => stage.event === "started" && stage.data.stage === "chat_started",
  );
  return (
    <section className="query-progress" role="status" aria-live="polite">
      <div className="query-progress-heading"><span><ArrowPathIcon className="spin" aria-hidden="true" /> {chatStarted ? "正在生成回答" : "正在检索并生成回答"}</span><button type="button" onClick={onStop}><XMarkIcon aria-hidden="true" /> 停止生成</button></div>
      {visibleStages.length ? <ol>{visibleStages.map((stage) => <li key={`${stage.request_id}-${stage.sequence}`}><CheckCircleIcon aria-hidden="true" />{stageLabel(stage)}</li>)}</ol> : <p>正在连接问答服务…</p>}
      <small>这里展示的是系统执行阶段，不是模型的思维链。</small>
    </section>
  );
}

export function cancelledQueryError() {
  return new ProductApiError("本次回答已停止。", "QUERY_CANCELLED", true);
}

export function displayHealthValue(value: number | string | null | undefined, format: (value: number | string) => string = String) {
  return value == null ? "尚无结论" : format(value);
}
