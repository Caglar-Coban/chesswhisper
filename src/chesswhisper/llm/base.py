from abc import ABC, abstractmethod


class LLMProviderError(Exception):
    pass


class LLMProvider(ABC):
    @abstractmethod
    def generate_commentary(self, report, lang: str) -> str:
        raise NotImplementedError
