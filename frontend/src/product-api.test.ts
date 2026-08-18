import { afterEach, expect, it, vi } from "vitest";

import {
  type QueryStageEvent,
  ProductApiError,
  cancelMessageFeedback,
  streamMessage,
  submitMessageFeedback,
} from "./product-api";

const encoder = new TextEncoder();

function streamResponse(chunks: string[]) {
  return {
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it("跨网络分片解析阶段事件并返回最终消息", async () => {
  const started =
    'event: started\ndata: {"version":1,"request_id":"request-1","sequence":1,' +
    '"event":"started","data":{"stage":"query_started"}}\n\n';
  const retrieval =
    'event: retrieval\ndata: {"version":1,"request_id":"request-1","sequence":3,' +
    '"event":"retrieval","data":{"iteration":1,"retrieved_count":3}}\n\n';
  const webSearch =
    'event: web_search\ndata: {"version":1,"request_id":"request-1","sequence":2,' +
    '"event":"web_search","data":{"iteration":1,"status":"completed",' +
    '"provider":"tavily","query_count":1,"result_count":2,"error_code":null}}\n\n';
  const completed =
    'event: completed\ndata: {"version":1,"request_id":"request-1","sequence":4,' +
    '"event":"completed","data":{"user_message":{"id":"user-1"},' +
    '"assistant_message":{"id":"assistant-1","content":"安全回答"}}}\n\n';
  const payload = started + webSearch + retrieval + completed;
  const fetchMock = vi.fn().mockResolvedValue(
    streamResponse([
      payload.slice(0, 19),
      payload.slice(19, 117),
      payload.slice(117, 241),
      payload.slice(241),
    ]),
  );
  vi.stubGlobal(
    "fetch",
    fetchMock,
  );
  const stages: QueryStageEvent[] = [];

  const result = await streamMessage(
    "conversation-1",
    "问题",
    (stage) => stages.push(stage),
    new AbortController().signal,
    true,
  );

  expect(stages.map((stage) => stage.event)).toEqual([
    "started",
    "web_search",
    "retrieval",
  ]);
  expect(stages[1].data).toEqual({
    iteration: 1,
    status: "completed",
    provider: "tavily",
    query_count: 1,
    result_count: 2,
    error_code: null,
  });
  expect(stages[2].data).toEqual({ iteration: 1, retrieved_count: 3 });
  expect(result.assistant_message.content).toBe("安全回答");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    content: "问题",
    use_web_search: true,
  });
});

it("把流式错误转换成稳定的 ProductApiError", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      streamResponse([
        'event: error\ndata: {"version":1,"request_id":"request-error-1","sequence":0,' +
          '"event":"error","data":{"code":"VECTOR_DB_UNAVAILABLE",' +
          '"message":"向量检索服务暂时不可用，请稍后重试。","retryable":true}}\n\n',
      ]),
    ),
  );

  await expect(
    streamMessage("conversation-1", "问题", () => undefined),
  ).rejects.toMatchObject({
    code: "VECTOR_DB_UNAVAILABLE",
    retryable: true,
    requestId: "request-error-1",
    message: "向量检索服务暂时不可用，请稍后重试。",
  } satisfies Partial<ProductApiError>);
});

it("提交消息反馈调用 POST 并返回反馈状态", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ feedback: { vote: 1, cancelled: false } }),
  });
  vi.stubGlobal("fetch", fetchMock);

  const result = await submitMessageFeedback("conversation-1", "message-1", {
    vote: 1,
  });

  expect(result).toEqual({ feedback: { vote: 1, cancelled: false } });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/conversations/conversation-1/messages/message-1/feedback",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ vote: 1 }),
    }),
  );
});

it("取消消息反馈调用 DELETE", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  });
  vi.stubGlobal("fetch", fetchMock);

  await cancelMessageFeedback("conversation-1", "message-1");

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/conversations/conversation-1/messages/message-1/feedback",
    expect.objectContaining({ method: "DELETE" }),
  );
});
