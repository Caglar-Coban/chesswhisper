_STRINGS = {
    "report_title": {"tr": "chesswhisper Analiz Raporu", "en": "chesswhisper Analysis Report"},
    "player": {"tr": "Oyuncu", "en": "Player"},
    "overall_summary": {"tr": "Genel Özet", "en": "Overall Summary"},
    "acpl": {"tr": "Ortalama Centipawn Kaybı (ACPL)", "en": "Average Centipawn Loss (ACPL)"},
    "accuracy": {"tr": "Doğruluk", "en": "Accuracy"},
    "errors_by_phase": {"tr": "Faza Göre Hatalar", "en": "Errors by Phase"},
    "phase_opening": {"tr": "Açılış", "en": "Opening"},
    "phase_middlegame": {"tr": "Orta Oyun", "en": "Middlegame"},
    "phase_endgame": {"tr": "Final", "en": "Endgame"},
    "inaccuracy": {"tr": "Belirsizlik", "en": "Inaccuracy"},
    "mistake": {"tr": "Hata", "en": "Mistake"},
    "blunder": {"tr": "Gaf", "en": "Blunder"},
    "openings": {"tr": "Açılış Repertuarı", "en": "Opening Repertoire"},
    "opening_games": {"tr": "Oyun", "en": "Games"},
    "opening_record": {"tr": "G/M/B/?", "en": "W/L/D/?"},
    "time_management": {"tr": "Zaman Yönetimi", "en": "Time Management"},
    "time_management_unavailable": {
        "tr": "Bu PGN'de saat bilgisi bulunmadığı için zaman yönetimi analizi mevcut değil.",
        "en": "Time management analysis is not available because this PGN has no clock data.",
    },
    "low_time_error_rate": {
        "tr": "Zaman baskısında (<30sn) hata oranı", "en": "Error rate under time pressure (<30s)",
    },
    "normal_time_error_rate": {
        "tr": "Normal zamanda hata oranı", "en": "Error rate with normal time",
    },
    "not_enough_data": {"tr": "yeterli veri yok", "en": "not enough data"},
    "coaching_commentary": {"tr": "Koçluk Yorumu", "en": "Coaching Commentary"},
    "per_game_breakdown": {"tr": "Oyun Bazında Döküm", "en": "Per-Game Breakdown"},
    "game_result_win": {"tr": "Kazandı", "en": "Won"},
    "game_result_loss": {"tr": "Kaybetti", "en": "Lost"},
    "game_result_draw": {"tr": "Berabere", "en": "Draw"},
    "game_result_unknown": {"tr": "Bilinmiyor", "en": "Unknown"},
    "color_white": {"tr": "beyaz", "en": "white"},
    "color_black": {"tr": "siyah", "en": "black"},
    "top_mistakes": {"tr": "Kritik Hamleler", "en": "Critical Moves"},
    "phase_column": {"tr": "Faz", "en": "Phase"},
    "eco_column": {"tr": "ECO", "en": "ECO"},
    "vs_label": {"tr": "vs", "en": "vs"},
}


def t(key: str, lang: str) -> str:
    entry = _STRINGS[key]
    return entry[lang]
