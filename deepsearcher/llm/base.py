import ast
import inspect
import math
import re
from abc import ABC
from dataclasses import dataclass
from typing import Any, Dict, List, Literal


@dataclass(frozen=True)
class ChatOptions:
    """Provider-neutral controls and telemetry identity for one model call."""

    stage: str = "unspecified"
    thinking: bool | None = None
    max_tokens: int | None = None
    response_format: dict | None = None
    iteration: int | None = None


@dataclass(frozen=True)
class TokenUsage:
    """Normalized model usage. Reasoning tokens are a subset of output tokens."""

    input_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_input_tokens: int = 0
    usage_source: Literal["provider", "estimated", "unavailable"] = "unavailable"

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "estimated_input_tokens",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens must be a subset of output_tokens")


def _safe_usage_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _legacy_chat(llm: Any, messages: List[Dict]) -> "ChatResponse":
    """Call legacy and duck-typed providers without masking provider errors."""

    chat = llm.chat
    try:
        parameters = inspect.signature(chat).parameters
    except (TypeError, ValueError):
        parameters = {}
    supports_messages_keyword = "messages" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    return chat(messages=messages) if supports_messages_keyword else chat(messages)


def estimate_message_tokens(llm: Any, messages: List[Dict]) -> int:
    estimator = getattr(llm, "estimate_tokens", None)
    if callable(estimator):
        return max(int(estimator(messages) or 0), 1)
    return BaseLLM.estimate_tokens(llm, messages)


class ChatResponse(ABC):
    """
    Represents a response from a chat model.

    This class encapsulates the content of a response from a chat model
    along with information about token usage.

    Attributes:
        content: The text content of the response.
        total_tokens: The total number of tokens used in the request and response.
    """

    def __init__(
        self,
        content: str,
        total_tokens: int | None = None,
        *,
        usage: TokenUsage | None = None,
    ) -> None:
        """
        Initialize a ChatResponse object.

        Args:
            content: The text content of the response.
            total_tokens: The total number of tokens used in the request and response.
        """
        self.content = content
        normalized_total = _safe_usage_int(total_tokens)
        if usage is None:
            usage = TokenUsage(
                total_tokens=normalized_total,
                usage_source="unavailable",
            )
        elif total_tokens is not None and usage.total_tokens != normalized_total:
            raise ValueError("total_tokens must match usage.total_tokens")
        self.usage = usage
        self.total_tokens = usage.total_tokens
        self.model: str | None = None
        self.fallback_reason: str | None = None

    def __repr__(self) -> str:
        """
        Return a string representation of the ChatResponse.

        Returns:
            A string representation of the ChatResponse object.
        """
        return f"ChatResponse(content={self.content}, total_tokens={self.total_tokens})"


class BaseLLM(ABC):
    """
    Abstract base class for language model implementations.

    This class defines the interface for language model implementations,
    including methods for chat-based interactions and parsing responses.
    """

    def __init__(self):
        """
        Initialize a BaseLLM object.
        """
        pass

    def chat(self, messages: List[Dict]) -> ChatResponse:
        """
        Send a chat message to the language model and get a response.

        Args:
            messages: A list of message dictionaries, typically in the format
                     [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

        Returns:
            A ChatResponse object containing the model's response.
        """
        pass

    def chat_with_options(
        self,
        messages: List[Dict],
        options: ChatOptions | None = None,
    ) -> ChatResponse:
        """Compatibility entry point; providers may override option translation."""

        del options
        response = _legacy_chat(self, messages)
        if response.usage.estimated_input_tokens == 0:
            estimate = self.estimate_tokens(messages)
            response.usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                cache_hit_tokens=response.usage.cache_hit_tokens,
                cache_miss_tokens=response.usage.cache_miss_tokens,
                output_tokens=response.usage.output_tokens,
                reasoning_tokens=response.usage.reasoning_tokens,
                total_tokens=response.usage.total_tokens,
                estimated_input_tokens=estimate,
                usage_source=response.usage.usage_source,
            )
        return response

    def estimate_tokens(self, messages: List[Dict]) -> int:
        """Conservative deterministic estimate used for preflight budgeting."""

        total = 2
        for message in messages:
            total += 6
            for key in ("role", "name", "content"):
                value = message.get(key)
                if value is None:
                    continue
                text = str(value)
                cjk = len(re.findall(r"[\u3400-\u9fff]", text))
                non_cjk = len(re.sub(r"[\u3400-\u9fff]", "", text))
                total += cjk + math.ceil(non_cjk / 3)
        return max(total, 1)

    @staticmethod
    def literal_eval(response_content: str):
        """
        Parse a string response into a Python object using ast.literal_eval.

        This method attempts to extract and parse JSON or Python literals from the response content,
        handling various formats like code blocks and special tags.

        Args:
            response_content: The string content to parse.

        Returns:
            The parsed Python object.

        Raises:
            ValueError: If the response content cannot be parsed.
        """
        response_content = response_content.strip()

        response_content = BaseLLM.remove_think(response_content)

        try:
            if response_content.startswith("```") and response_content.endswith("```"):
                if response_content.startswith("```python"):
                    response_content = response_content[9:-3]
                elif response_content.startswith("```json"):
                    response_content = response_content[7:-3]
                elif response_content.startswith("```str"):
                    response_content = response_content[6:-3]
                elif response_content.startswith("```\n"):
                    response_content = response_content[4:-3]
                else:
                    raise ValueError("Invalid code block format")
            result = ast.literal_eval(response_content.strip())
        except Exception:
            matches = re.findall(r"(\[.*?\]|\{.*?\})", response_content, re.DOTALL)

            if len(matches) != 1:
                raise ValueError(
                    f"Invalid JSON/List format for response content:\n{response_content}"
                )

            json_part = matches[0]
            return ast.literal_eval(json_part)

        return result

    @staticmethod
    def remove_think(response_content: str) -> str:
        # remove content between <think> and </think>, especial for reasoning model
        if "<think>" in response_content and "</think>" in response_content:
            end_of_think = response_content.find("</think>") + len("</think>")
            response_content = response_content[end_of_think:]
        return response_content.strip()


def chat_with_stage(
    llm: BaseLLM,
    messages: List[Dict],
    *,
    stage: str,
    max_tokens: int,
    thinking: bool = False,
    iteration: int | None = None,
    response_format: dict | None = None,
    trace_collector=None,
    input_evidence_count: int = 0,
    input_evidence_tokens: int = 0,
) -> ChatResponse:
    """Invoke a model with a named cost policy and record sanitized telemetry."""

    optional_stages = {
        "agent_router",
        "collection_router",
        "history_rewrite",
        "query_decomposition",
        "rerank",
        "support_filter",
        "reflection",
        "followup_query",
        "intermediate_answer",
    }
    estimated_input = estimate_message_tokens(llm, messages)
    effective_max_tokens = max_tokens
    reserve = getattr(trace_collector, "reserve_llm_call", None)
    if callable(reserve):
        effective_max_tokens = reserve(
            stage=stage,
            estimated_input_tokens=estimated_input,
            requested_max_tokens=max_tokens,
            optional=stage in optional_stages,
        )
        if effective_max_tokens <= 0:
            fallback = {
                "agent_router": "invalid",
                "collection_router": "[]",
                "history_rewrite": "{}",
                "query_decomposition": "[]",
                "rerank": "[]",
                "support_filter": "[]",
                "reflection": "[]",
                "followup_query": "",
                "intermediate_answer": "No relevant information found",
            }.get(stage)
            if fallback is not None:
                return ChatResponse(content=fallback, total_tokens=0)
            raise RuntimeError("LLM token budget exhausted before required stage")
    options = ChatOptions(
        stage=stage,
        thinking=thinking,
        max_tokens=effective_max_tokens,
        response_format=response_format,
        iteration=iteration,
    )
    option_chat = getattr(llm, "chat_with_options", None)
    try:
        response = (
            option_chat(messages, options) if callable(option_chat) else _legacy_chat(llm, messages)
        )
    except BaseException:
        release = getattr(trace_collector, "release_llm_reservation", None)
        if callable(release):
            release(effective_max_tokens, estimated_input)
        raise
    if trace_collector is not None:
        trace_collector.record_llm_call(
            stage=stage,
            iteration=iteration,
            model=str(response.model or getattr(llm, "model", llm.__class__.__name__)),
            fallback_reason=response.fallback_reason,
            thinking=thinking,
            max_tokens=effective_max_tokens,
            usage=response.usage,
            input_evidence_count=input_evidence_count,
            input_evidence_tokens=input_evidence_tokens,
        )
    return response
