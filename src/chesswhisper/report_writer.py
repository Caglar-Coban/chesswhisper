from chesswhisper.i18n import t
from chesswhisper.insights import key_findings, render_finding
from chesswhisper.motifs import OTHER
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


def _segment_table(segments, label_column: str, label_prefix: str, lang: str) -> list[str]:
    lines = [
        f"| {t(label_column, lang)} | {t('opening_games', lang)} | {t('opening_record', lang)} "
        f"| {t('acpl_column', lang)} | {t('accuracy', lang)} |",
        "| --- | --- | --- | --- | --- |",
    ]
    for seg in segments:
        unknown = seg.games - seg.wins - seg.losses - seg.draws
        lines.append(
            f"| {t(label_prefix + seg.label, lang)} | {seg.games} | {seg.wins}/{seg.losses}/{seg.draws}/{unknown} "
            f"| {seg.acpl:.1f} | {seg.accuracy:.1f}% |"
        )
    return lines


def _pattern_table(buckets, label_column: str, label_for, lang: str) -> list[str]:
    lines = [
        f"| {t(label_column, lang)} | {t('moves_column', lang)} | {t('errors_column', lang)} | {t('error_rate_column', lang)} |",
        "| --- | --- | --- | --- |",
    ]
    for bucket in buckets:
        rate = f"{bucket.error_rate:.0%}" if bucket.error_rate is not None else "-"
        lines.append(f"| {label_for(bucket.label)} | {bucket.moves} | {bucket.errors} | {rate} |")
    return lines


def _signed_pawns(cp: float | None) -> str:
    if cp is None:
        return "-"
    sign = "+" if cp >= 0 else "−"
    return f"{sign}{abs(cp) / 100:.1f}"


def _theory_line(theory, lang: str) -> str | None:
    if theory is None:
        return None
    if theory.left_by is None:
        return t("theory_stayed", lang)
    text = t(f"theory_left_{theory.left_by}", lang).format(move=theory.exit_move, san=theory.exit_san)
    evals = t("theory_eval", lang).format(at=_signed_pawns(theory.eval_at_exit), later=_signed_pawns(theory.eval_later))
    return f"{text}; {evals}"


def describe_filter(game_filter, lang: str) -> str:
    """ "Blitz, Rapid · as Black" in the report's language."""
    parts = []
    if game_filter.time_classes:
        parts.append(", ".join(t(f"tc_{tc}", lang) for tc in game_filter.ordered_time_classes))
    if game_filter.color is not None:
        parts.append(t(f"filter_as_{game_filter.color}", lang))
    return " · ".join(parts)


def render_markdown(report, commentary: str, lang: str) -> str:
    parts = [f"# {t('report_title', lang)}", ""]
    parts.append(f"**{t('player', lang)}:** {report.player}")
    if report.game_filter is not None:
        parts.append("")
        parts.append(f"**{t('filter_label', lang)}:** {describe_filter(report.game_filter, lang)}")
    parts.append("")
    parts.append(f"## {t('overall_summary', lang)}")
    parts.append(f"- {t('acpl', lang)}: {report.overall_acpl:.1f}")
    parts.append(f"- {t('accuracy', lang)}: {report.overall_accuracy:.1f}%")
    parts.append("")
    parts.append(f"### {t('errors_by_phase', lang)}")
    parts.append(_errors_table(report.error_totals, lang))
    parts.append("")

    findings = key_findings(report)
    if findings:
        parts.append(f"## {t('key_findings', lang)}")
        parts.extend(f"- {render_finding(finding, lang)}" for finding in findings)
        parts.append("")

    parts.append(f"## {t('openings', lang)}")
    parts.append(f"| {t('eco_column', lang)} | {t('opening_column', lang)} | {t('opening_games', lang)} | {t('opening_record', lang)} |")
    parts.append("| --- | --- | --- | --- |")
    for op in report.openings:
        parts.append(
            f"| {op.eco} | {op.name} | {op.games} | {op.wins}/{op.losses}/{op.draws}/{op.unknown} |"
        )
    parts.append("")

    if any(row.you_left or row.opponent_left for row in report.opening_theory):
        parts.append(f"## {t('opening_theory', lang)}")
        parts.append(t("opening_theory_note", lang))
        parts.append("")
        parts.append(
            f"| {t('opening_column', lang)} | {t('opening_games', lang)} | {t('book_depth_column', lang)} | "
            f"{t('left_by_column', lang)} | {t('eval_at_exit_column', lang)} | {t('change_column', lang)} |"
        )
        parts.append("| --- | --- | --- | --- | --- | --- |")
        for row in report.opening_theory:
            parts.append(
                f"| {row.name} | {row.games} | {row.avg_book_moves:.1f} | {row.you_left} / {row.opponent_left} | "
                f"{_signed_pawns(row.avg_eval_at_exit)} | {_signed_pawns(row.avg_change)} |"
            )
        parts.append("")

    if report.recent_form is not None:
        parts.append(f"## {t('recent_form', lang)}")
        parts.append(t("recent_form_note", lang))
        parts.append("")
        parts.append(
            f"| {t('period_column', lang)} | {t('dates_column', lang)} | {t('accuracy', lang)} | "
            f"{t('acpl_column', lang)} | {t('blunders_per_game_column', lang)} | {t('score_column', lang)} |"
        )
        parts.append("| --- | --- | --- | --- | --- | --- |")
        for key, period in (("period_recent", report.recent_form.recent), ("period_earlier", report.recent_form.earlier)):
            score = f"{period.score:.0%}" if period.score is not None else "-"
            parts.append(
                f"| {t(key, lang).format(n=period.games)} | {period.first:%Y-%m-%d} – {period.last:%Y-%m-%d} | "
                f"{period.accuracy:.1f}% | {period.acpl:.1f} | {period.blunders_per_game:.1f} | {score} |"
            )
        parts.append("")

    if report.by_time_class:
        parts.append(f"## {t('by_time_class', lang)}")
        parts.extend(_segment_table(report.by_time_class, "time_class_column", "tc_", lang))
        parts.append("")

    if report.by_opponent_strength:
        parts.append(f"## {t('by_opponent_strength', lang)}")
        parts.extend(_segment_table(report.by_opponent_strength, "opponent_column", "strength_", lang))
        parts.append("")

    if any(bucket.moves for bucket in report.move_ranges):
        parts.append(f"## {t('error_patterns', lang)}")
        parts.append(t("error_patterns_note", lang))
        parts.append("")
        parts.extend(_pattern_table(report.move_ranges, "move_range_column", lambda label: label, lang))
        parts.append("")
        parts.extend(
            _pattern_table(report.eval_states, "eval_state_column", lambda label: t(f"state_label_{label}", lang), lang)
        )
        parts.append("")

    if report.motifs:
        parts.append(f"## {t('tactical_patterns', lang)}")
        parts.append(t("tactical_patterns_note", lang))
        parts.append("")
        parts.append(
            f"| {t('motif_column', lang)} | {t('allowed_column', lang)} | {t('missed_column', lang)} | {t('total_column', lang)} |"
        )
        parts.append("| --- | --- | --- | --- |")
        for stat in report.motifs:
            allowed, missed = (stat.allowed, stat.missed) if stat.motif != OTHER else ("-", "-")
            parts.append(f"| {t('motif_' + stat.motif, lang)} | {allowed} | {missed} | {stat.total} |")
        parts.append("")

    if report.chances.total:
        chances = report.chances
        parts.append(f"## {t('opponent_errors', lang)}")
        parts.append(t("opponent_errors_summary", lang).format(
            total=chances.total, punished=chances.punished, pct=round((chances.punish_rate or 0) * 100)))
        if chances.missed_by_motif:
            parts.append("")
            parts.append(t("opponent_errors_missed_note", lang))
            parts.append("")
            parts.append(f"| {t('motif_column', lang)} | {t('missed_column', lang)} |")
            parts.append("| --- | --- |")
            for motif, count in chances.missed_by_motif:
                parts.append(f"| {t('motif_' + motif, lang)} | {count} |")
        parts.append("")

    if report.endgames:
        parts.append(f"## {t('endgames', lang)}")
        parts.append(t("endgames_note", lang))
        parts.append("")
        parts.append(
            f"| {t('endgame_column', lang)} | {t('opening_games', lang)} | {t('converted_column', lang)} | "
            f"{t('held_column', lang)} | {t('saved_column', lang)} | {t('acpl_column', lang)} |"
        )
        parts.append("| --- | --- | --- | --- | --- | --- |")
        for row in report.endgames:
            parts.append(
                f"| {t('endgame_' + row.kind, lang)} | {row.games} | {row.converted} / {row.winning} | "
                f"{row.held} / {row.level} | {row.saved} / {row.losing} | {row.acpl:.1f} |"
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
        theory_line = _theory_line(game.theory, lang)
        if theory_line:
            parts.append(f"- {theory_line}")
        if game.endgame is not None:
            parts.append("- " + t("game_endgame", lang).format(
                kind=t("endgame_" + game.endgame.kind, lang).lower(), move=game.endgame.move_number,
                eval=_signed_pawns(game.endgame.eval_at_entry)))
        if game.chances:
            punished = sum(1 for chance in game.chances if chance.punished)
            parts.append(f"- {t('game_chances', lang).format(punished=punished, total=len(game.chances))}")
        parts.append(f"- {t('acpl', lang)}: {game.your_stats.acpl:.1f}")
        parts.append(f"- {t('accuracy', lang)}: {game.your_stats.accuracy:.1f}%")
        if game.top_mistakes:
            parts.append(f"- {t('top_mistakes', lang)}: " + ", ".join(m.move_san for m in game.top_mistakes))
        # Blank lines keep each block separate: a table or heading glued to the
        # list above would be swallowed into its last item by Markdown renderers.
        parts.append("")
        parts.append(f"**{t('errors_by_phase', lang)}:**")
        parts.append("")
        parts.append(_errors_table(game.your_stats.errors_by_phase, lang))
        parts.append("")
        parts.append(f"**{t('time_management', lang)}:**")
        parts.append("")
        parts.extend(_time_management_lines(game.time_management, lang))
        parts.append("")

    return "\n".join(parts)


def write_report(report, commentary: str, lang: str, output_path: str) -> None:
    markdown = render_markdown(report, commentary, lang)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
