"""
Corner prediction orchestrator.
Combines features + scorer into a final prediction.
"""
from dataclasses import dataclass, field
from typing import Optional
from analysis.features import MatchContext, LiveStats, compute_derived_signals
from analysis.scorer import score_signals, confidence_to_odds, top_contributors
from config import MIN_CONFIDENCE, MIN_ODDS, OVER_LINE

# Pre-match: flag match for live watch at lower threshold
PRE_MATCH_WATCH_THRESHOLD = OVER_LINE - 1.5


@dataclass
class CornerPrediction:
    market: str = ""
    prediction: str = "Over"
    confidence: float = 0.0
    estimated_odds: float = 0.0
    projected_final_corners: float = 0.0
    current_corners: int = 0
    minute: int = 0
    mode: str = "pre_match"
    top_signals: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    score: float = 0.0
    reasoning: str = ""


def analyse_pre_match(ctx: MatchContext) -> Optional[CornerPrediction]:
    """
    Pre-match analysis — flags matches worth watching.
    Does not push bet alerts.
    Returns prediction if projected corners > watch threshold.
    """
    ctx.live = None
    signals = compute_derived_signals(ctx)
    signals["_matches_played"] = (
        ctx.home_profile.matches_played + ctx.away_profile.matches_played
    )

    result = score_signals(signals, is_live=False)

    # Use h2h + historical average as pre-match projection
    hist_avg = (
        ctx.home_profile.avg_corners_for +
        ctx.away_profile.avg_corners_for +
        ctx.home_profile.avg_corners_against +
        ctx.away_profile.avg_corners_against
    ) / 2

    h2h_avg = ctx.h2h.avg_total_corners
    projected = hist_avg * 0.7 + h2h_avg * 0.3 if h2h_avg > 0 else hist_avg

    if projected < PRE_MATCH_WATCH_THRESHOLD and result["confidence"] < 0.55:
        return None

    return CornerPrediction(
        market=f"Over {OVER_LINE} Total Corners",
        confidence=result["confidence"],
        estimated_odds=confidence_to_odds(result["confidence"]),
        projected_final_corners=round(projected, 1),
        mode="pre_match",
        top_signals=top_contributors(result["contributions"]),
        signals=signals,
        score=result["score"],
        reasoning=_build_reasoning(ctx, signals, result, projected, is_live=False),
    )


LIVE_CHECKPOINTS = [25, 35, 45, 55, 65, 75]


def analyse_live(ctx: MatchContext) -> Optional[CornerPrediction]:
    """
    Live analysis at a checkpoint minute.
    Returns a prediction if it meets confidence + odds thresholds.
    """
    live = ctx.live
    if not live:
        return None

    minute = live.minute
    last = ctx.last_alert_minute

    # Only fire at checkpoints
    at_checkpoint = any(
        minute >= cp and (last is None or last < cp)
        for cp in LIVE_CHECKPOINTS
    )
    if not at_checkpoint:
        return None

    signals = compute_derived_signals(ctx)
    signals["_matches_played"] = (
        ctx.home_profile.matches_played + ctx.away_profile.matches_played
    )

    result = score_signals(signals, is_live=True)
    projected = signals.get("projected_corners_live", 0)

    if result["confidence"] < MIN_CONFIDENCE:
        return None

    current_total = live.home_corners + live.away_corners

    return CornerPrediction(
        market=f"Over {OVER_LINE} Total Corners",
        confidence=result["confidence"],
        estimated_odds=confidence_to_odds(result["confidence"]),
        projected_final_corners=round(projected, 1),
        current_corners=current_total,
        minute=minute,
        mode="live",
        top_signals=top_contributors(result["contributions"]),
        signals=signals,
        score=result["score"],
        reasoning=_build_reasoning(ctx, signals, result, projected, is_live=True),
    )


def _build_reasoning(
    ctx: MatchContext,
    signals: dict,
    result: dict,
    projected: float,
    is_live: bool,
) -> str:
    live = ctx.live
    parts = []

    if is_live and live:
        total = live.home_corners + live.away_corners
        parts.append(
            f"Minute {live.minute}: {total} corners ({live.home_corners}–{live.away_corners}), "
            f"shots {live.home_shots_total}–{live.away_shots_total}, "
            f"possession {live.home_possession:.0f}%–{live.away_possession:.0f}%."
        )
        parts.append(f"Projected final: {projected:.1f} corners.")
        if signals.get("urgency", 1) > 1.05:
            parts.append("Score urgency detected — chasing team pushing forward.")
        if signals.get("corner_shot_gap", 0) > 1.5:
            parts.append("Shots significantly ahead of corners — corners may catch up.")
        if signals.get("corner_momentum", 1) > 1.2:
            parts.append("Corner rate accelerating in second half.")
    else:
        hp = ctx.home_profile
        ap = ctx.away_profile
        parts.append(
            f"{ctx.home_team} avg {hp.avg_corners_for:.1f} corners for / "
            f"{hp.avg_corners_against:.1f} against. "
            f"{ctx.away_team} avg {ap.avg_corners_for:.1f} / {ap.avg_corners_against:.1f}."
        )
        if ctx.h2h.avg_total_corners > 0:
            parts.append(
                f"H2H avg {ctx.h2h.avg_total_corners:.1f} corners, "
                f"{ctx.h2h.over_line_rate*100:.0f}% over {OVER_LINE}."
            )
        if signals.get("crossing_intensity", 0) > 20:
            parts.append("Both teams have high crossing tendencies.")
        if signals.get("referee_corner_avg", 10) > 12:
            parts.append(f"Referee averages {signals['referee_corner_avg']:.1f} corners/game.")

    top = top_contributors(result["contributions"], n=3)
    if top:
        parts.append("Key drivers: " + ", ".join(k for k, _ in top) + ".")

    return " ".join(parts)
