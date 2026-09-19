import pytest

from chesswhisper.i18n import t


def test_known_key_turkish():
    assert t("report_title", "tr") == "chesswhisper Analiz Raporu"


def test_known_key_english():
    assert t("report_title", "en") == "chesswhisper Analysis Report"


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        t("does_not_exist", "tr")


def test_unsupported_lang_raises():
    with pytest.raises(KeyError):
        t("report_title", "fr")
