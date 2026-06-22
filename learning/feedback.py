from typing import Optional
"""
Learning system: checks results for pending predictions and adjusts model weights.
"""
import logging
import aiohttp
from data.database import (
    get_pending_predictions, resolve_prediction,
    update_weight, log_accuracy,
)
from data import api_client

logger = logging.getLogger(__name__)


async def check_and_resolve(session: aiohttp.ClientSession):
    """
    For each pending prediction whose match has passed, fetch result and update weights.
    """
    pending = get_pending_predictions()
    if not pending:
        logger.info("No pending predictions to resolve.")
        return

    by_league: dict[str, dict] = {}
    resolved_count = 0

    for pred in pending:
        fixture_id = pred["fixture_id"]
        try:
            stats = await api_client.get_fixture_statistics(session, fixture_id)
            if not stats:
                continue

            fh_corners = _extract_fh_corners(stats)
            if fh_corners is None:
                continue  # match not finished or no corner data

            outcome = "won" if fh_corners > 4.5 else "lost"
            result_str = f"{fh_corners} FH corners"

            resolve_prediction(pred["id"], result_str, outcome)

            league = pred["league_name"] or "Unknown"
            if league not in by_league:
                by_league[league] = {"total": 0, "correct": 0}
            by_league[league]["total"] += 1
            if outcome == "won":
                by_league[league]["correct"] += 1

            correct = outcome == "won"
            _update_weights_for_features(pred, correct)
            resolved_count += 1
            logger.info(
                "Resolved fixture %d: %s corners=%s outcome=%s",
                fixture_id, pred["home_team"] + " vs " + pred["away_team"],
                fh_corners, outcome,
            )

        except Exception as e:
            logger.error("Error resolving fixture %d: %s", fixture_id, e)

    for league, counts in by_league.items():
        log_accuracy(league, counts["total"], counts["correct"])

    logger.info("Resolved %d predictions.", resolved_count)


def _extract_fh_corners(stats: list) -> Optional[float]:
    """Sum first-half corners from fixture statistics response."""
    total = 0
    found = False
    for team_stat in stats:
        for s in team_stat.get("statistics", []):
            if "corner" in s.get("type", "").lower():
                val = s.get("value")
                if val is not None:
                    try:
                        total += int(val)
                        found = True
                    except (ValueError, TypeError):
                        pass
    # Note: API-Football free tier may not return half-time corner breakdown.
    # We use full-match corners as proxy; improve with paid tier.
    return total / 2.0 if found else None  # crude half-time estimate


def _update_weights_for_features(pred, correct: bool):
    """
    Update weights based on which features dominated the prediction.
    We bump the most impactful features.
    """
    import json
    try:
        if isinstance(pred["stats_snapshot"], str):
            snap = json.loads(pred["stats_snapshot"])
        else:
            snap = pred["stats_snapshot"] or {}
    except Exception:
        snap = {}

    # Features present with non-zero values get updated
    feature_map = {
        # Historical signals
        "combined_attack_output":  "avg_corners_for",
        "h2h_corner_richness":     "h2h_corners",
        "h2h_over_rate":           "h2h_corners",
        "referee_corner_avg":      "referee_corners",
        "crossing_intensity":      "form_last5",
        # Live signals — get stronger weight updates
        "live_corner_rate":        "live_corners",
        "corner_momentum":         "live_corners",
        "live_shot_rate":          "live_shots",
        "corner_per_shot":         "live_shots",
        "live_cross_rate":         "live_crosses",
        "cross_to_corner_ratio":   "live_crosses",
        "live_attack_rate":        "live_attacks",
        "match_intensity":         "live_attacks",
    }
    updated = set()
    for snap_key, weight_key in feature_map.items():
        if snap.get(snap_key) and weight_key not in updated:
            update_weight(weight_key, correct)
            updated.add(weight_key)


def build_stats_report(stats_row) -> str:
    """Format overall bot performance for /stats command."""
    if not stats_row:
        return "No predictions resolved yet."

    total = stats_row["total"] or 0
    wins = stats_row["wins"] or 0
    losses = stats_row["losses"] or 0
    win_rate = (stats_row["win_rate"] or 0) * 100

    return (
        f"📊 *Bot Performance*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total predictions: {total}\n"
        f"✅ Won: {wins}\n"
        f"❌ Lost: {losses}\n"
        f"🎯 Win rate: {win_rate:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
