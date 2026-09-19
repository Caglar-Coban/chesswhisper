import pytest

from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.openai_provider import OpenAIProvider


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=stats, opponent_stats=stats, eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False), top_mistakes=[],
    )
    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors, openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1)],
        time_management=TimeManagementStats(available=False),
    )


def test_generate_commentary_returns_text(mocker):
    fake_message = mocker.Mock()
    fake_message.content = "İyi oynadın!"
    fake_choice = mocker.Mock()
    fake_choice.message = fake_message
    fake_response = mocker.Mock()
    fake_response.choices = [fake_choice]

    fake_client = mocker.Mock()
    fake_client.chat.completions.create.return_value = fake_response

    provider = OpenAIProvider(api_key="fake-key")
    provider._client = fake_client

    result = provider.generate_commentary(_sample_report(), lang="tr")

    assert result == "İyi oynadın!"


def test_generate_commentary_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.chat.completions.create.side_effect = RuntimeError("boom")

    provider = OpenAIProvider(api_key="fake-key")
    provider._client = fake_client

    with pytest.raises(LLMProviderError):
        provider.generate_commentary(_sample_report(), lang="tr")


def test_generate_commentary_wraps_malformed_response(mocker):
    fake_response = mocker.Mock()
    fake_response.choices = []  # atypical SDK response (e.g. a content filter) -> IndexError

    fake_client = mocker.Mock()
    fake_client.chat.completions.create.return_value = fake_response

    provider = OpenAIProvider(api_key="fake-key")
    provider._client = fake_client

    with pytest.raises(LLMProviderError):
        provider.generate_commentary(_sample_report(), lang="tr")


def test_missing_api_key_raises_at_construction(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMProviderError):
        OpenAIProvider(api_key=None)
