import chess
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.claude_provider import ClaudeProvider


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    top_mistake = PositionEval(
        ply=10, move_san="Nf3", color=chess.WHITE, cp_before=20, cp_after=-180,
        fen_after=chess.STARTING_FEN,
    )
    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=stats, eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False), top_mistakes=[top_mistake],
    )
    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors, openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1)],
        time_management=TimeManagementStats(available=False),
    )


def test_generate_commentary_returns_text(mocker):
    fake_response = mocker.Mock()
    fake_response.content = [mocker.Mock(text="Harika bir açılış oynadın!")]

    fake_client = mocker.Mock()
    fake_client.messages.create.return_value = fake_response

    provider = ClaudeProvider(api_key="fake-key")
    provider._client = fake_client  # inject the mocked SDK client

    result = provider.generate_commentary(_sample_report(), lang="tr")

    assert result == "Harika bir açılış oynadın!"
    fake_client.messages.create.assert_called_once()


def test_generate_commentary_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.messages.create.side_effect = RuntimeError("boom")

    provider = ClaudeProvider(api_key="fake-key")
    provider._client = fake_client

    with pytest.raises(LLMProviderError):
        provider.generate_commentary(_sample_report(), lang="tr")


def test_generate_commentary_wraps_malformed_response(mocker):
    fake_response = mocker.Mock()
    fake_response.content = []  # atypical SDK response (e.g. a refusal) -> IndexError

    fake_client = mocker.Mock()
    fake_client.messages.create.return_value = fake_response

    provider = ClaudeProvider(api_key="fake-key")
    provider._client = fake_client

    with pytest.raises(LLMProviderError):
        provider.generate_commentary(_sample_report(), lang="tr")


def test_missing_api_key_raises_at_construction(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMProviderError):
        ClaudeProvider(api_key=None)
