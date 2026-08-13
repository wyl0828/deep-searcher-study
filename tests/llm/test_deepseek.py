import logging
import os
import unittest
from unittest.mock import MagicMock, patch

# Disable logging for tests
logging.disable(logging.CRITICAL)

from deepsearcher.llm import DeepSeek  # noqa: E402
from deepsearcher.llm.base import ChatOptions, ChatResponse  # noqa: E402


class TestDeepSeek(unittest.TestCase):
    """Tests for the DeepSeek LLM provider."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock module and components
        self.mock_openai = MagicMock()
        self.mock_client = MagicMock()
        self.mock_chat = MagicMock()
        self.mock_completions = MagicMock()

        # Set up the mock module structure
        self.mock_openai.OpenAI = MagicMock(return_value=self.mock_client)
        self.mock_client.chat = self.mock_chat
        self.mock_chat.completions = self.mock_completions

        # Set up mock response
        self.mock_response = MagicMock()
        self.mock_choice = MagicMock()
        self.mock_message = MagicMock()
        self.mock_usage = MagicMock()

        self.mock_message.content = "Test response"
        self.mock_choice.message = self.mock_message
        self.mock_usage.total_tokens = 100

        self.mock_response.choices = [self.mock_choice]
        self.mock_response.usage = self.mock_usage
        self.mock_completions.create.return_value = self.mock_response

        # Create the module patcher
        self.module_patcher = patch.dict("sys.modules", {"openai": self.mock_openai})
        self.module_patcher.start()

    def tearDown(self):
        """Clean up test fixtures."""
        self.module_patcher.stop()

    def test_init_default(self):
        """Test initialization with default parameters."""
        # Clear environment variables temporarily
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek()
            # Check that OpenAI client was initialized correctly
            self.mock_openai.OpenAI.assert_called_once_with(
                api_key=None, base_url="https://api.deepseek.com"
            )

            # Check default model
            self.assertEqual(llm.model, "deepseek-v4-flash")

    def test_init_with_api_key_from_env(self):
        """Test initialization with API key from environment variable."""
        api_key = "test_api_key_from_env"
        base_url = "https://custom.deepseek.api"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": api_key, "DEEPSEEK_BASE_URL": base_url}):
            DeepSeek()
            self.mock_openai.OpenAI.assert_called_with(api_key=api_key, base_url=base_url)

    def test_init_with_api_key_parameter(self):
        """Test initialization with API key as parameter."""
        api_key = "test_api_key_param"
        with patch.dict("os.environ", {}, clear=True):
            DeepSeek(api_key=api_key)
            self.mock_openai.OpenAI.assert_called_with(
                api_key=api_key, base_url="https://api.deepseek.com"
            )

    def test_init_with_custom_model(self):
        """Test initialization with custom model."""
        with patch.dict("os.environ", {}, clear=True):
            model = "deepseek-v4-pro"
            llm = DeepSeek(model=model)
            self.assertEqual(llm.model, model)

    def test_init_with_custom_base_url(self):
        """Test initialization with custom base URL."""
        # Clear environment variables temporarily
        with patch.dict("os.environ", {}, clear=True):
            base_url = "https://custom.deepseek.api"
            DeepSeek(base_url=base_url)
            self.mock_openai.OpenAI.assert_called_with(api_key=None, base_url=base_url)

    def test_chat_single_message(self):
        """Test chat with a single message."""
        # Create DeepSeek instance with mocked environment
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek()

        messages = [{"role": "user", "content": "Hello"}]
        response = llm.chat(messages)

        # Check that completions.create was called correctly
        self.mock_completions.create.assert_called_once()
        call_args = self.mock_completions.create.call_args
        self.assertEqual(call_args[1]["model"], "deepseek-v4-flash")
        self.assertEqual(call_args[1]["messages"], messages)
        self.assertEqual(call_args[1]["temperature"], 0.0)

        # Check response
        self.assertIsInstance(response, ChatResponse)
        self.assertEqual(response.content, "Test response")
        self.assertEqual(response.total_tokens, 100)

    def test_chat_multiple_messages(self):
        """Test chat with multiple messages."""
        # Create DeepSeek instance with mocked environment
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek()

        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        response = llm.chat(messages)

        # Check that completions.create was called correctly
        self.mock_completions.create.assert_called_once()
        call_args = self.mock_completions.create.call_args
        self.assertEqual(call_args[1]["model"], "deepseek-v4-flash")
        self.assertEqual(call_args[1]["messages"], messages)

        # Check response
        self.assertIsInstance(response, ChatResponse)
        self.assertEqual(response.content, "Test response")
        self.assertEqual(response.total_tokens, 100)

    def test_chat_with_error(self):
        """Test chat when an error occurs."""
        # Create DeepSeek instance with mocked environment
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek()

        # Mock an error response
        self.mock_completions.create.side_effect = Exception("DeepSeek API Error")

        messages = [{"role": "user", "content": "Hello"}]
        with self.assertRaises(Exception) as context:
            llm.chat(messages)

        self.assertEqual(str(context.exception), "DeepSeek API Error")

    def test_v4_non_thinking_options_and_usage_breakdown(self):
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek(model="deepseek-v4-flash")
        self.mock_usage.prompt_tokens = 80
        self.mock_usage.prompt_cache_hit_tokens = 50
        self.mock_usage.prompt_cache_miss_tokens = 30
        self.mock_usage.completion_tokens = 20
        self.mock_usage.total_tokens = 100
        self.mock_usage.completion_tokens_details.reasoning_tokens = 7

        response = llm.chat_with_options(
            [{"role": "user", "content": "Hello"}],
            ChatOptions(stage="rerank", thinking=False, max_tokens=256),
        )

        request = self.mock_completions.create.call_args.kwargs
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(request["max_tokens"], 256)
        self.assertEqual(response.usage.output_tokens, 20)
        self.assertEqual(response.usage.reasoning_tokens, 7)
        self.assertEqual(response.total_tokens, 100)

    def test_v4_thinking_omits_temperature(self):
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek(model="deepseek-v4-pro")
        llm.chat_with_options(
            [{"role": "user", "content": "Hello"}],
            ChatOptions(stage="final_answer", thinking=True, max_tokens=4096),
        )
        request = self.mock_completions.create.call_args.kwargs
        self.assertEqual(request["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertNotIn("temperature", request)

    def test_missing_usage_retains_estimate(self):
        with patch.dict("os.environ", {}, clear=True):
            llm = DeepSeek()
        self.mock_response.usage = None
        response = llm.chat_with_options(
            [{"role": "user", "content": "预算估算"}],
            ChatOptions(stage="history_rewrite", thinking=False, max_tokens=256),
        )
        self.assertEqual(response.total_tokens, 0)
        self.assertEqual(response.usage.usage_source, "estimated")
        self.assertGreater(response.usage.estimated_input_tokens, 0)


if __name__ == "__main__":
    unittest.main()
