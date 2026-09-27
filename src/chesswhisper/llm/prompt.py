from chesswhisper.insights import key_findings, render_finding
from chesswhisper.motifs import OTHER
from chesswhisper.stats_engine import cp_loss

_LANG_INSTRUCTION = {
    "tr": "Yanıtını Türkçe yaz.",
    "en": "Write your response in English.",
}


def build_prompt(report, lang: str) -> str:
    lines = [
        f"Player: {report.player}",
        f"Overall ACPL: {report.overall_acpl:.1f}",
        f"Overall accuracy: {report.overall_accuracy:.1f}%",
        "Error totals by phase:",
    ]
    for phase, counts in report.error_totals.items():
        lines.append(f"  {phase}: inaccuracies={counts.inaccuracy}, mistakes={counts.mistake}, blunders={counts.blunder}")

    lines.append("Opening repertoire:")
    for op in report.openings:
        lines.append(f"  {op.name} ({op.eco}): {op.games} games, {op.wins}W/{op.losses}L/{op.draws}D")

    theory_rows = [row for row in report.opening_theory if row.you_left or row.opponent_left]
    if theory_rows:
        lines.append("Leaving opening theory, per opening (evals in centipawns from the player's side):")
        for row in theory_rows:
            lines.append(
                f"  {row.name}: {row.games} games, {row.avg_book_moves:.1f} moves in book on average, "
                f"left by the player {row.you_left}x and by the opponent {row.opponent_left}x, "
                f"eval on leaving {row.avg_eval_at_exit or 0:+.0f}, "
                f"change over the next 10 moves {row.avg_change or 0:+.0f}"
            )
    if report.by_time_class:
        lines.append("By time control:")
        for seg in report.by_time_class:
            lines.append(
                f"  {seg.label}: {seg.games} games, {seg.wins}W/{seg.losses}L/{seg.draws}D, "
                f"ACPL {seg.acpl:.1f}, accuracy {seg.accuracy:.1f}%"
            )
    if report.by_opponent_strength:
        lines.append("By opponent rating (relative to the player):")
        for seg in report.by_opponent_strength:
            lines.append(f"  {seg.label}: {seg.games} games, {seg.wins}W/{seg.losses}L/{seg.draws}D")
    if any(bucket.moves for bucket in report.move_ranges):
        lines.append("Share of moves that were mistakes or blunders, by move number:")
        for bucket in report.move_ranges:
            if bucket.moves:
                lines.append(f"  moves {bucket.label}: {bucket.errors}/{bucket.moves}")
        lines.append("Same, by evaluation before the move (ahead/level/behind by 2 pawns):")
        for bucket in report.eval_states:
            if bucket.moves:
                lines.append(f"  {bucket.label}: {bucket.errors}/{bucket.moves}")
    if report.motifs:
        lines.append(
            "Tactical motifs behind the player's mistakes and blunders (allowed = the opponent's tactic "
            "the move permitted, missed = the player's tactic the engine preferred):"
        )
        for stat in report.motifs:
            if stat.motif == OTHER:
                lines.append(f"  no clear tactic (positional or deeper): {stat.total}")
            else:
                lines.append(f"  {stat.motif}: allowed {stat.allowed}, missed {stat.missed}")
    if report.chances.total:
        chances = report.chances
        missed = ", ".join(f"{motif} {count}" for motif, count in chances.missed_by_motif) or "none"
        lines.append(
            f"Opponents' mistakes and blunders: {chances.total}, punished by the player: {chances.punished}; "
            f"missed ones by the tactic the reply needed: {missed}"
        )
    findings = key_findings(report)
    if findings:
        lines.append("Automatically detected patterns:")
        lines.extend(f"  - {render_finding(finding, 'en')}" for finding in findings)

    lines.append("Most critical mistakes across games:")
    for game in report.games:
        for mistake in game.top_mistakes:
            lines.append(
                f"  Game {game.game_index + 1}, move {mistake.move_san} (ply {mistake.ply}): "
                f"lost {cp_loss(mistake)} cp, resulting position FEN: {mistake.fen_after}"
            )

    instruction = _LANG_INSTRUCTION[lang]
    lines.append("")
    lines.append(
        f"You are a supportive chess coach. Using only the data above, write a short, concrete "
        f"coaching summary (3-5 short paragraphs) explaining this player's main strengths, their "
        f"most important weaknesses, and 1-2 specific things to practice next. {instruction}"
    )
    return "\n".join(lines)
