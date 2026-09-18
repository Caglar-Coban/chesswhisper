from chesswhisper.llm.none_provider import NoneProvider


def test_none_provider_returns_empty_string():
    provider = NoneProvider()
    assert provider.generate_commentary(report=None, lang="tr") == ""
