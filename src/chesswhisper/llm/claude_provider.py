import os

import anthropic

from chesswhisper.llm.base import LLMProvider, LLMProviderError
from chesswhisper.llm.prompt import build_prompt


class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5"):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMProviderError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY or pass --llm none."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model

    def generate_commentary(self, report, lang: str) -> str:
        prompt = build_prompt(report, lang)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as exc:  # noqa: BLE001 - any SDK failure becomes a provider error
            raise LLMProviderError(f"Claude API call failed: {exc}") from exc
