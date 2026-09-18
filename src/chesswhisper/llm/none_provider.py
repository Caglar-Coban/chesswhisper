from chesswhisper.llm.base import LLMProvider


class NoneProvider(LLMProvider):
    def generate_commentary(self, report, lang: str) -> str:
        return ""
