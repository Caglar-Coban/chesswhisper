from chesswhisper.i18n import t
from chesswhisper.stats_engine import PHASES

_RESULT_KEY = {
    "win": "game_result_win",
    "loss": "game_result_loss",
    "draw": "game_result_draw",
    "unknown": "game_result_unknown",
}
_PHASE_KEY = {"opening": "phase_opening", "middlegame": "phase_middlegame", "endgame": "phase_endgame"}
_COLOR_KEY = {"white": "color_white", "black": "color_black"}


def _errors_table(errors_by_phase: dict, lang: str) -> str:
    lines = [
        "| " + t("phase_column", lang) + " | " + t("inaccuracy", lang) + " | " + t("mistake", lang) + " | " + t("blunder", lang) + " |",
        "| --- | --- | --- | --- |",
    ]
    for phase in PHASES:
        counts = errors_by_phase[phase]
        lines.append(
            f"| {t(_PHASE_KEY[phase], lang)} | {counts.inaccuracy} | {counts.mistake} | {counts.blunder} |"
        )
    return "\n".join(lines)


def _time_management_lines(tm, lang: str) -> list[str]:
    lines = []
    if tm.available:
        low_rate = tm.low_time_error_rate
        normal_rate = tm.normal_time_error_rate
        low_str = f"{low_rate:.1%}" if low_rate is not None else t("not_enough_data", lang)
        normal_str = f"{normal_rate:.1%}" if normal_rate is not None else t("not_enough_data", lang)
        lines.append(f"- {t('low_time_error_rate', lang)}: {low_str}")
        lines.append(f"- {t('normal_time_error_rate', lang)}: {normal_str}")
    else:
        lines.append(t("time_management_unavailable", lang))
    return lines


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
    parts.append(f"| {t('eco_column', lang)} | {t('openings', lang)} | {t('opening_games', lang)} | {t('opening_record', lang)} |")
    parts.append("| --- | --- | --- | --- |")
    for op in report.openings:
        parts.append(
            f"| {op.eco} | {op.name} | {op.games} | {op.wins}/{op.losses}/{op.draws}/{op.unknown} |"
        )
    parts.append("")

    parts.append(f"## {t('time_management', lang)}")
    parts.extend(_time_management_lines(report.time_management, lang))
    parts.append("")

    parts.append(f"## {t('coaching_commentary', lang)}")
    parts.append(commentary if commentary else "-")
    parts.append("")

    parts.append(f"## {t('per_game_breakdown', lang)}")
    for game in report.games:
        result_str = t(_RESULT_KEY[game.result], lang)
        color_str = t(_COLOR_KEY[game.your_color], lang)
        parts.append(
            f"### {game.game_index + 1}. {t('vs_label', lang)} {game.opponent} ({result_str}, {color_str})"
        )
        parts.append(f"- {game.opening_name} ({game.eco})")
        parts.append(f"- {t('acpl', lang)}: {game.your_stats.acpl:.1f}")
        parts.append(f"- {t('accuracy', lang)}: {game.your_stats.accuracy:.1f}%")
        game_errors_table = _errors_table(game.your_stats.errors_by_phase, lang)
        parts.append(f"**{t('errors_by_phase', lang)}:**")
        parts.append(game_errors_table)
        parts.append(f"**{t('time_management', lang)}:**")
        parts.extend(_time_management_lines(game.time_management, lang))
        if game.top_mistakes:
            parts.append(f"- {t('top_mistakes', lang)}: " + ", ".join(m.move_san for m in game.top_mistakes))
        parts.append("")

    return "\n".join(parts)


def write_report(report, commentary: str, lang: str, output_path: str) -> None:
    markdown = render_markdown(report, commentary, lang)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
