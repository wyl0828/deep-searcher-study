from typing import Dict, List

from deepsearcher.llm.base import BaseLLM, ChatResponse


class Aliyun(BaseLLM):
    """
    Aliyun language model implementation.

    This class provides an interface to interact with language models
    hosted on the Aliyun platform.

    API Documentation: https://bailian.console.aliyun.com/?tab=api#/api

    Attributes:
        model (str): The model identifier to use on Aliyun platform.
        client: The OpenAI-compatible client instance for Aliyun API.
    """

    def __init__(self, model: str = "deepseek-r1", **kwargs):
        """
        Initialize an Aliyun language model client.

        Args:
            model (str, optional): The model identifier to use. Defaults to "deepseek-r1".
            **kwargs: Additional keyword arguments to pass to the OpenAI client.
                - api_key: Aliyun bailian API key. If not provided, uses DASHSCOPE_API_KEY environment variable.
                - base_url: Aliyun bailian API base URL. If not provided, defaults to "https://dashscope.aliyuncs.com/compatible-mode/v1".
        """
        from openai import OpenAI as OpenAI_

        from deepsearcher.llm._credentials import resolve_api_key, resolve_base_url

        self.model = model

        api_key = resolve_api_key(kwargs, provider_env="DASHSCOPE_API_KEY")
        base_url = resolve_base_url(
            kwargs, default="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        self.client = OpenAI_(api_key=api_key, base_url=base_url, **kwargs)

    def chat(self, messages: List[Dict]) -> ChatResponse:
        """
        Send a chat message to the Aliyun model and get a response.

        Args:
            messages (List[Dict]): A list of message dictionaries, typically in the format
                                  [{"role": "system", "content": "..."},
                                   {"role": "user", "content": "..."}]

        Returns:
            ChatResponse: An object containing the model's response and token usage information.
        """
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return ChatResponse(
            content=completion.choices[0].message.content,
            total_tokens=completion.usage.total_tokens,
        )
