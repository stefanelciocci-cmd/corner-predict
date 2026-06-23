from typing import Optional, List
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
from config import OVER_LINE

logger = logging.getLogger(__name__)


async def check_and_resolve(session: aiohttp.ClientSession) -> List[dict]:
    """
    For each pending prediction whose match has finished, fetch the result,
    update model weights, and return result notifications to push to users.
    """
    pending = get_pending_predictions()
    if not pending:
        logger.info("No pending predictions to resolve.")
        return []

    by_league = {}
    notifications = []

    for pred in pending:
        fixture_id = pred["fixture_id"]
        try:
            fixture = await api_client.get_fixture_by_id(session, fixture_id)
            if not fixture:
                continue

            status = api_client.get_fixture_status(fixture)
            if status not in ("FT", "AET", "PEN"):
                continue  # match not finished yet

            stats = await api_client.get_fixture_statistics(session, fixture_id)
            if not stats:
                continue

            total_corners = _extract_total_corners(stats)
            if total_corners is None:
                continue

            won = total_corners > OVER_LINE
            outcome = "won" if won else "lost"
            result_str = f"{total_corners} total corners"

            resolve_prediction(pred["id"], result_str, outcome)

            league = pred["league_name"] or "Unknown"
            by_league.setdefault(league, {"total": 0, "correct": 0})
            by_league[league]["total"] += 1
            if won:
                by_league[league]["correct"] += 1

            _update_weights_for_features(pred, won)

            # Build result notification
            emoji = "✅" if won else "❌"
            result_label = "WON" if won else "LOST"
            notifications.append(
                f"{emoji} *Prediction Result — {result_label}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚽ *{pred['home_team']}* vs *{pred['away_team']}*\n"
                f"🏆 {league}\n"
                f"📋 Market: Over {OVER_LINE} Total Corners\n"
                f"🔢 Final corners: *{total_corners}* "
                f"({'✅ over' if won else '❌ under'} {OVER_LINE})\n"
                f"🎯 Our confidence was: {pred['confidence']*100:.0f}%"
            )

            logger.info(
                "Resolved %s vs %s: %s corners → %s",
                pred["home_team"], pred["away_team"], total_corners, outcome,
            )

        except Exception as e:
            logger.error("Error resolving fixture %d: %s", fixture_id, e)

    for league, counts in by_league.items():
        log_accuracy(league, counts["total"], counts["correct"])

    logger.info("Resolved %d predictions.", len(notifications))
    return notifications


def _extract_total_corners(stats: list) -> Optional[int]:
    """Sum total corners from both teams in fixture statistics."""
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
    return total if found else None


def _update_weights_for_features(pred, correct: bool):
    import json
    try:
        snap = json.loads(pred["stats_snapshot"]) if isinstance(pred["stats_snapshot"], str) else (pred["stats_snapshot"] or {})
    except Exception:
        snap = {}

    feature_map = {
        "combined_attack_output":  "avg_corners_for",
        "h2h_corner_richness":     "h2h_corners",
        "h2h_over_rate":           "h2h_corners",
        "referee_corner_avg":      "referee_corners",
        "crossing_intensity":      "form_last5",
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
    if not stats_row:
        return "No predictions resolved yet."

    total = stats_row["total"] or 0
    wins = stats_row["wins"] or 0
    losses = stats_row["losses"] or 0
    win_rate = (stats_row["win_rate"] or 0) * 100
    pending = total - wins - losses

    return (
        f"📊 *Bot Performance*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total predictions: *{total}*\n"
        f"✅ Won: *{wins}*\n"
        f"❌ Lost: *{losses}*\n"
        f"⏳ Pending: *{pending}*\n"
        f"🎯 Win rate: *{win_rate:.1f}%*\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
