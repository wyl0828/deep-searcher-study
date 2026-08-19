from typing import Dict, List

from deepsearcher.llm.base import (
    BaseLLM,
    ChatOptions,
    ChatResponse,
    TokenUsage,
    _safe_usage_int,
)
from deepsearcher.llm.routing import StreamEvent


class _DeepSeekStream:
    def __init__(self, stream):
        self.stream = stream
        self.iterator = iter(stream)

    def __iter__(self):
        return self

    def __next__(self) -> StreamEvent:
        while True:
            chunk = next(self.iterator)
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = choices[0].delta
            content = getattr(delta, "content", None)
            if content is not None:
                return StreamEvent("content", str(content))
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is not None:
                return StreamEvent("thinking", str(reasoning))

    def cancel(self) -> None:
        close = getattr(self.stream, "close", None)
        if callable(close):
            close()


class DeepSeek(BaseLLM):
    """
    DeepSeek language model implementation.

    This class provides an interface to interact with DeepSeek's language models
    through their API. DeepSeek offers powerful reasoning capabilities.

    API Documentation: https://api-docs.deepseek.com/

    Attributes:
        model (str): The DeepSeek model identifier to use.
        client: The OpenAI-compatible client instance for DeepSeek API.
    """

    def __init__(self, model: str = "deepseek-v4-flash", **kwargs):
        """
        Initialize a DeepSeek language model client.

        Args:
            model (str, optional): The model identifier to use. Defaults to "deepseek-v4-flash".
            **kwargs: Additional keyword arguments to pass to the OpenAI client.
                - api_key: DeepSeek API key. If not provided, uses DEEPSEEK_API_KEY environment variable.
                - base_url: DeepSeek API base URL. If not provided, uses DEEPSEEK_BASE_URL environment
                  variable or defaults to "https://api.deepseek.com".
        """
        from openai import OpenAI as OpenAI_

        from deepsearcher.llm._credentials import resolve_api_key, resolve_base_url

        self.model = model
        api_key = resolve_api_key(kwargs, provider_env="DEEPSEEK_API_KEY")
        base_url = resolve_base_url(
            kwargs, provider_env="DEEPSEEK_BASE_URL", default="https://api.deepseek.com"
        )
        self.temperature = float(kwargs.pop("temperature", 0.0))
        self.client = OpenAI_(api_key=api_key, base_url=base_url, **kwargs)

    def chat(self, messages: List[Dict]) -> ChatResponse:
        """
        Send a chat message to the DeepSeek model and get a response.

        Args:
            messages (List[Dict]): A list of message dictionaries, typically in the format
                                  [{"role": "system", "content": "..."},
                                   {"role": "user", "content": "..."}]

        Returns:
            ChatResponse: An object containing the model's response and token usage information.
        """
        return self.chat_with_options(messages)

    def chat_with_options(
        self,
        messages: List[Dict],
        options: ChatOptions | None = None,
    ) -> ChatResponse:
        options = options or ChatOptions()
        stable_system = {
            "agent_router": "Select exactly one valid agent index. Return only the index.",
            "collection_router": "Select authorized collection names. Return only a JSON array.",
            "history_rewrite": "Rewrite conversational questions using the required JSON contract.",
            "query_decomposition": "Return only the requested bounded JSON query list.",
            "rerank": "Return only valid zero-based evidence indices as a JSON array.",
            "support_filter": "Return only fully supported evidence indices as a JSON array.",
            "reflection": "Assess evidence gaps and return only the requested constrained output.",
            "followup_query": "Return one concise follow-up search query without explanation.",
            "intermediate_answer": "Answer only from supplied evidence and stay concise.",
            "entailment": "Apply the strict entailment JSON contract without explanation.",
            "final_answer": "Answer only from supplied evidence and preserve citation markers.",
        }.get(options.stage)
        request_messages = list(messages)
        if stable_system and not (request_messages and request_messages[0].get("role") == "system"):
            request_messages = [
                {"role": "system", "content": stable_system},
                *request_messages,
            ]
        request: Dict = {"model": self.model, "messages": request_messages}
        if options.thinking is None:
            request["temperature"] = self.temperature
        else:
            request["extra_body"] = {
                "thinking": {"type": "enabled" if options.thinking else "disabled"}
            }
            if not options.thinking:
                request["temperature"] = self.temperature
        if options.max_tokens is not None:
            request["max_tokens"] = max(int(options.max_tokens), 1)
        if options.response_format is not None:
            request["response_format"] = options.response_format
        completion = self.client.chat.completions.create(**request)
        raw_usage = getattr(completion, "usage", None)
        input_tokens = _safe_usage_int(getattr(raw_usage, "prompt_tokens", 0))
        cache_hit = _safe_usage_int(getattr(raw_usage, "prompt_cache_hit_tokens", 0))
        cache_miss = _safe_usage_int(getattr(raw_usage, "prompt_cache_miss_tokens", 0))
        output_tokens = _safe_usage_int(getattr(raw_usage, "completion_tokens", 0))
        details = getattr(raw_usage, "completion_tokens_details", None)
        reasoning_tokens = min(
            _safe_usage_int(getattr(details, "reasoning_tokens", 0)),
            output_tokens,
        )
        total_tokens = _safe_usage_int(getattr(raw_usage, "total_tokens", 0))
        usage_available = any((input_tokens, cache_hit, cache_miss, output_tokens, total_tokens))
        estimate = self.estimate_tokens(messages)
        if not usage_available:
            usage_source = "estimated"
        else:
            usage_source = "provider"
        return ChatResponse(
            content=completion.choices[0].message.content,
            usage=TokenUsage(
                input_tokens=input_tokens,
                cache_hit_tokens=cache_hit,
                cache_miss_tokens=cache_miss,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                estimated_input_tokens=estimate,
                usage_source=usage_source,
            ),
        )

    def stream_with_options(
        self,
        messages: List[Dict],
        options: ChatOptions | None = None,
    ) -> _DeepSeekStream:
        options = options or ChatOptions()
        request: Dict = {"model": self.model, "messages": list(messages), "stream": True}
        if options.thinking is None:
            request["temperature"] = self.temperature
        else:
            request["extra_body"] = {
                "thinking": {"type": "enabled" if options.thinking else "disabled"}
            }
            if not options.thinking:
                request["temperature"] = self.temperature
        if options.max_tokens is not None:
            request["max_tokens"] = max(int(options.max_tokens), 1)
        if options.response_format is not None:
            request["response_format"] = options.response_format
        return _DeepSeekStream(self.client.chat.completions.create(**request))
