import os

import openai

from chesswhisper.llm.base import LLMProvider, LLMProviderError
from chesswhisper.llm.prompt import build_prompt


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini"):
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMProviderError(
                "No OpenAI API key found. Set OPENAI_API_KEY or pass --llm none."
            )
        self._client = openai.OpenAI(api_key=key)
        self._model = model

    def generate_commentary(self, report, lang: str) -> str:
        prompt = build_prompt(report, lang)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"OpenAI API call failed: {exc}") from exc

        if content is None:
            raise LLMProviderError("OpenAI returned an empty response (no text content).")
        return content
