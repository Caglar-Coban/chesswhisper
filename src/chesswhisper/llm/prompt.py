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
