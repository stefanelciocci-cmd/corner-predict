"""
LLM analysis via Groq REST API (no SDK — avoids httpx version conflicts).
Model: llama-3.1-70b-versatile, free tier: 14,400 req/day.
"""
import logging
import requests
from config import GROQ_API_KEY, OVER_LINE

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
}

SYSTEM_PROMPT = (
    "You are an expert football betting analyst specialising in corners markets. "
    "You receive rich match statistics and produce concise, data-driven predictions. "
    "Be direct. Use bullet points. Never invent data not given to you. "
    "End with: verdict (STRONG BET / MODERATE BET / AVOID) and one-sentence alert summary."
)


def _build_prompt(ctx, pred) -> str:
    hp = ctx.home_profile
    ap = ctx.away_profile
    h2h = ctx.h2h
    live = ctx.live
    signals = pred.signals
    top = pred.top_signals

    lines = [
        f"**{ctx.home_team} vs {ctx.away_team}** — {ctx.league_name}",
        f"**Market:** Over {OVER_LINE} Total Corners",
        f"**Mode:** {'LIVE' if live else 'Pre-match'}",
        "",
        "**Historical Team Profiles:**",
        (
            f"- {ctx.home_team}: {hp.avg_corners_for:.1f} corners/game for, "
            f"{hp.avg_corners_against:.1f} against | "
            f"shots {hp.avg_shots_total:.1f}/game | "
            f"crosses {hp.avg_crosses:.1f}/game | "
            f"possession {hp.avg_possession:.0f}%"
        ),
        (
            f"- {ctx.away_team}: {ap.avg_corners_for:.1f} corners/game for, "
            f"{ap.avg_corners_against:.1f} against | "
            f"shots {ap.avg_shots_total:.1f}/game | "
            f"crosses {ap.avg_crosses:.1f} | "
            f"possession {ap.avg_possession:.0f}%"
        ),
        "",
    ]

    if h2h.matches_analysed > 0:
        lines += [
            f"**Head-to-Head (last {h2h.matches_analysed} games):**",
            (
                f"- Avg total corners: {h2h.avg_total_corners:.1f} | "
                f"Over {OVER_LINE} rate: {h2h.over_line_rate*100:.0f}% | "
                f"11+ corners rate: {h2h.corner_rich_rate*100:.0f}%"
            ),
            f"- Typical pace: {h2h.typical_pace}",
            "",
        ]

    if live:
        total_c = live.home_corners + live.away_corners
        lines += [
            f"**Live (Minute {live.minute}):**",
            f"- Score: {ctx.home_team} {live.home_score}–{live.away_score} {ctx.away_team}",
            f"- Corners: {live.home_corners}–{live.away_corners} (total: {total_c})",
            f"- Shots: {live.home_shots_total}–{live.away_shots_total}",
            f"- Possession: {live.home_possession:.0f}%–{live.away_possession:.0f}%",
            f"- Dangerous attacks: {live.home_dangerous_attacks}–{live.away_dangerous_attacks}",
            f"- Fouls: {live.home_fouls}–{live.away_fouls}",
        ]
        if signals.get("urgency", 1) > 1.05:
            lines.append("- Score urgency: one team is chasing the game")
        if signals.get("corner_shot_gap", 0) > 1.5:
            lines.append(f"- Shots-to-corners gap: {signals['corner_shot_gap']:.1f} corners 'owed'")
        lines.append("")

    lines += [
        "**Model Output:**",
        f"- Projected final corners: {pred.projected_final_corners:.1f}",
        f"- Confidence: {pred.confidence*100:.1f}%",
    ]
    if top:
        lines.append(f"- Top drivers: {', '.join(k for k, _ in top[:4])}")

    lines += [
        "",
        "Provide:",
        "1. 3-4 bullets: key factors FOR this bet",
        "2. 1-2 bullets: risk factors AGAINST",
        "3. Verdict: STRONG BET / MODERATE BET / AVOID",
        "4. One-sentence alert summary (punchy, for Telegram)",
        "Keep under 200 words.",
    ]
    return "\n".join(lines)


def get_llm_analysis(ctx, pred) -> dict:
    try:
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(ctx, pred)},
            ],
            "max_tokens": 400,
            "temperature": 0.3,
        }
        resp = requests.post(GROQ_URL, headers=HEADERS, json=payload, timeout=15)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        verdict = "MODERATE BET"
        summary = lines[-1] if lines else pred.reasoning
        for line in lines:
            u = line.upper()
            if "STRONG BET" in u:
                verdict = "STRONG BET"
            elif "AVOID" in u:
                verdict = "AVOID"
            elif "MODERATE BET" in u:
                verdict = "MODERATE BET"

        return {"full_analysis": text, "summary": summary, "verdict": verdict}

    except Exception as e:
        logger.error("Groq LLM failed: %s", e)
        return {
            "full_analysis": "",
            "summary": pred.reasoning,
            "verdict": "MODERATE BET",
        }


def format_alert_message(ctx, pred, llm_result: dict) -> str:
    verdict_emoji = {
        "STRONG BET": "🔥", "MODERATE BET": "✅", "AVOID": "⚠️"
    }.get(llm_result.get("verdict", ""), "✅")

    is_live = pred.mode == "live"
    live = ctx.live
    conf_pct = round(pred.confidence * 100, 1)
    bar = "█" * int(conf_pct / 10) + "░" * (10 - int(conf_pct / 10))
    tag = "🔴 *LIVE ALERT*" if is_live else "📬 *PRE-MATCH ALERT*"

    lines = [
        f"{verdict_emoji} {tag}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏟 *{ctx.home_team}* vs *{ctx.away_team}*",
        f"🏆 {ctx.league_name}",
    ]

    if is_live and live:
        total_c = live.home_corners + live.away_corners
        lines += [
            f"⏱ *Minute {live.minute}* | Score: {live.home_score}–{live.away_score}",
            "",
            "📐 *Live Stats*",
            f"⚽ Corners: `{live.home_corners}–{live.away_corners}` (total {total_c})",
            f"🎯 Shots: `{live.home_shots_total}–{live.away_shots_total}`",
            f"🔄 Possession: `{live.home_possession:.0f}%–{live.away_possession:.0f}%`",
            f"↗️ Dangerous attacks: `{live.home_dangerous_attacks}–{live.away_dangerous_attacks}`",
        ]
        if pred.signals.get("urgency", 1) > 1.05:
            lines.append("⚡ Urgency: team chasing the game")
    else:
        hp, ap = ctx.home_profile, ctx.away_profile
        lines += [
            "",
            "📊 *Team Stats*",
            f"{ctx.home_team}: {hp.avg_corners_for:.1f} corners | {hp.avg_shots_total:.1f} shots | {hp.avg_crosses:.1f} crosses/game",
            f"{ctx.away_team}: {ap.avg_corners_for:.1f} corners | {ap.avg_shots_total:.1f} shots | {ap.avg_crosses:.1f} crosses/game",
        ]
        if ctx.h2h.matches_analysed > 0:
            lines.append(
                f"🔄 H2H avg: {ctx.h2h.avg_total_corners:.1f} corners | "
                f"{ctx.h2h.over_line_rate*100:.0f}% over {OVER_LINE}"
            )

    lines += [
        "",
        f"📊 *Market:* {pred.market}",
        f"🔮 *Projected:* {pred.projected_final_corners:.1f} corners",
        f"💰 *Est. Odds:* {pred.estimated_odds:.2f}+",
        "",
        f"📈 *Confidence:* {conf_pct}%",
        f"`[{bar}]`",
        "",
        f"💡 _{llm_result.get('summary', pred.reasoning)}_",
        "",
        f"*Verdict:* {llm_result.get('verdict', 'MODERATE BET')}",
        "━━━━━━━━━━━━━━━━━━━━",
        "_Bet responsibly. Past performance ≠ future results._",
    ]
    return "\n".join(lines)
