from chesswhisper.i18n import t
from chesswhisper.stats_engine import PHASES

_RESULT_KEY = {"win": "game_result_win", "loss": "game_result_loss", "draw": "game_result_draw"}
_PHASE_KEY = {"opening": "phase_opening", "middlegame": "phase_middlegame", "endgame": "phase_endgame"}


def _errors_table(errors_by_phase: dict, lang: str) -> str:
    lines = [
        "| Faza / Phase | " + t("inaccuracy", lang) + " | " + t("mistake", lang) + " | " + t("blunder", lang) + " |",
        "| --- | --- | --- | --- |",
    ]
    for phase in PHASES:
        counts = errors_by_phase[phase]
        lines.append(
            f"| {t(_PHASE_KEY[phase], lang)} | {counts.inaccuracy} | {counts.mistake} | {counts.blunder} |"
        )
    return "\n".join(lines)


def render_markdown(report, commentary: str, lang: str) -> str:
    parts = [f"# {t('report_title', lang)}", ""]
    parts.append(f"**{t('player', lang)}:** {report.player}")
    parts.append("")
    parts.append(f"## {t('overall_summary', lang)}")
    parts.append(f"- {t('acpl', lang)}: {report.overall_acpl:.1f}")
    parts.append(f"- {t('accuracy', lang)}: {report.overall_accuracy:.1f}%")
    parts.append("")
    parts.append(f"### {t('errors_by_phase', lang)}")
    parts.append(_errors_table(report.error_totals, lang))
    parts.append("")

    parts.append(f"## {t('openings', lang)}")
    parts.append(f"| Eco | {t('openings', lang)} | {t('opening_games', lang)} | {t('opening_record', lang)} |")
    parts.append("| --- | --- | --- | --- |")
    for op in report.openings:
        parts.append(f"| {op.eco} | {op.name} | {op.games} | {op.wins}/{op.losses}/{op.draws} |")
    parts.append("")

    parts.append(f"## {t('time_management', lang)}")
    if report.time_management.available:
        low_rate = report.time_management.low_time_error_rate
        normal_rate = report.time_management.normal_time_error_rate
        low_str = f"{low_rate:.1%}" if low_rate is not None else t("not_enough_data", lang)
        normal_str = f"{normal_rate:.1%}" if normal_rate is not None else t("not_enough_data", lang)
        parts.append(f"- {t('low_time_error_rate', lang)}: {low_str}")
        parts.append(f"- {t('normal_time_error_rate', lang)}: {normal_str}")
    else:
        parts.append(t("time_management_unavailable", lang))
    parts.append("")

    parts.append(f"## {t('coaching_commentary', lang)}")
    parts.append(commentary if commentary else "-")
    parts.append("")

    parts.append(f"## {t('per_game_breakdown', lang)}")
    for game in report.games:
        result_str = t(_RESULT_KEY[game.result], lang)
        parts.append(
            f"### {game.game_index + 1}. vs {game.opponent} ({result_str}, {game.your_color})"
        )
        parts.append(f"- {game.opening_name} ({game.eco})")
        parts.append(f"- {t('acpl', lang)}: {game.your_stats.acpl:.1f}")
        parts.append(f"- {t('accuracy', lang)}: {game.your_stats.accuracy:.1f}%")
        if game.top_mistakes:
            parts.append(f"- {t('top_mistakes', lang)}: " + ", ".join(m.move_san for m in game.top_mistakes))
        parts.append("")

    return "\n".join(parts)


def write_report(report, commentary: str, lang: str, output_path: str) -> None:
    markdown = render_markdown(report, commentary, lang)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
