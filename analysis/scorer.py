"""
Scoring model: turns a signals dict into a corner probability and confidence.

Each signal has a weight (learned over time via feedback).
Signals are normalised to [0,1] and combined into a weighted score.
The score maps to a probability of exceeding OVER_LINE corners.
"""
import math
from typing import List, Tuple
from data.database import get_weights
from config import OVER_LINE


# Expected "neutral" values per signal — used for normalisation
# (calibrated from typical European football statistics)
SIGNAL_NORMS = {
    # Historical
    "combined_attack_output":    {"neutral": 18.0,  "scale": 8.0,  "direction": 1},
    "crossing_intensity":        {"neutral": 18.0,  "scale": 8.0,  "direction": 1},
    "block_tendency":            {"neutral": 4.0,   "scale": 2.0,  "direction": 1},
    "foul_pressure":             {"neutral": 22.0,  "scale": 6.0,  "direction": 1},
    "press_proxy":               {"neutral": 3.0,   "scale": 2.0,  "direction": 1},
    "possession_imbalance":      {"neutral": 10.0,  "scale": 10.0, "direction": 1},
    "h2h_corner_richness":       {"neutral": 10.0,  "scale": 4.0,  "direction": 1},
    "h2h_over_rate":             {"neutral": 0.50,  "scale": 0.25, "direction": 1},
    "h2h_corner_rich_rate":      {"neutral": 0.30,  "scale": 0.25, "direction": 1},
    "referee_corner_avg":        {"neutral": 10.0,  "scale": 3.0,  "direction": 1},
    "referee_foul_avg":          {"neutral": 22.0,  "scale": 5.0,  "direction": 1},
    # Live
    "projected_corners_live":    {"neutral": 9.5,   "scale": 3.0,  "direction": 1},
    "live_corner_rate":          {"neutral": 0.11,  "scale": 0.06, "direction": 1},
    "live_shot_rate":            {"neutral": 0.25,  "scale": 0.10, "direction": 1},
    "live_cross_rate":           {"neutral": 0.20,  "scale": 0.10, "direction": 1},
    "live_attack_rate":          {"neutral": 1.20,  "scale": 0.60, "direction": 1},
    "live_foul_rate":            {"neutral": 0.25,  "scale": 0.10, "direction": 1},
    "corner_per_shot":           {"neutral": 0.22,  "scale": 0.10, "direction": 1},
    "cross_to_corner_ratio":     {"neutral": 0.55,  "scale": 0.25, "direction": 1},
    "match_intensity":           {"neutral": 1.50,  "scale": 0.70, "direction": 1},
    "urgency":                   {"neutral": 1.00,  "scale": 0.20, "direction": 1},
    "corner_momentum":           {"neutral": 1.00,  "scale": 0.40, "direction": 1},
    "corner_shot_gap":           {"neutral": 0.00,  "scale": 1.50, "direction": 1},
}

# DB weight keys mapped to which signals they govern
WEIGHT_TO_SIGNALS = {
    "avg_corners_for":    ["combined_attack_output", "h2h_corner_richness"],
    "avg_corners_against":["h2h_over_rate", "h2h_corner_rich_rate"],
    "h2h_corners":        ["h2h_corner_richness", "h2h_over_rate", "h2h_corner_rich_rate"],
    "referee_corners":    ["referee_corner_avg", "referee_foul_avg"],
    "form_last5":         ["crossing_intensity", "block_tendency", "foul_pressure"],
    "live_corners":       ["live_corner_rate", "corner_momentum", "projected_corners_live"],
    "live_shots":         ["live_shot_rate", "corner_per_shot", "corner_shot_gap"],
    "live_crosses":       ["live_cross_rate", "cross_to_corner_ratio"],
    "live_attacks":       ["live_attack_rate", "match_intensity", "urgency"],
}


def _normalise(value: float, neutral: float, scale: float, direction: int) -> float:
    """Map a raw signal to [-1, 1] relative to neutral."""
    raw = (value - neutral) / scale
    return max(-2.0, min(2.0, raw)) * direction


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def score_signals(signals: dict, is_live: bool = False) -> dict:
    """
    Returns:
      score       — raw weighted sum (positive = over line more likely)
      confidence  — sigmoid-mapped probability [0, 1]
      contrib     — per-signal contributions (for explainability)
    """
    db_weights = get_weights()

    # Build signal → weight mapping
    sig_weights: dict[str, float] = {}
    for db_key, sig_list in WEIGHT_TO_SIGNALS.items():
        w = db_weights.get(db_key, 1.0)
        for sig in sig_list:
            sig_weights[sig] = w

    # Live signals get a higher base weight — they are observed, not predicted
    live_boost = 1.6 if is_live else 1.0
    live_signal_keys = {k for k in SIGNAL_NORMS if k.startswith("live_") or
                        k in ("corner_per_shot", "cross_to_corner_ratio",
                               "match_intensity", "urgency", "corner_momentum",
                               "corner_shot_gap")}

    weighted_sum = 0.0
    total_weight = 0.0
    contrib = {}

    for sig_name, norm_params in SIGNAL_NORMS.items():
        value = signals.get(sig_name)
        if value is None:
            continue

        base_w = sig_weights.get(sig_name, 1.0)
        w = base_w * (live_boost if sig_name in live_signal_keys else 1.0)

        normalised = _normalise(value, **norm_params)
        contribution = normalised * w
        weighted_sum += contribution
        total_weight += w
        contrib[sig_name] = round(contribution, 3)

    # Normalise to [-3, 3] range then sigmoid
    if total_weight > 0:
        avg_score = weighted_sum / total_weight * 3.0
    else:
        avg_score = 0.0

    # For live mode, incorporate projected corners directly
    if is_live and "projected_corners_live" in signals:
        proj = signals["projected_corners_live"]
        proj_signal = (proj - OVER_LINE) / OVER_LINE * 2.0
        avg_score = avg_score * 0.6 + proj_signal * 0.4

    confidence = round(_sigmoid(avg_score), 3)

    # Data quality penalty only in pre-match mode (live data is observed, not predicted)
    if not is_live:
        matches = signals.get("_matches_played", 20)
        quality = min(1.0, matches / 10)
        confidence = round(confidence * (0.85 + 0.15 * quality), 3)

    return {
        "score": round(avg_score, 3),
        "confidence": confidence,
        "contributions": contrib,
    }


def confidence_to_odds(confidence: float) -> float:
    if confidence <= 0.01:
        return 99.0
    return round((1 / confidence) * 0.95, 2)


def top_contributors(contrib: dict, n: int = 5) -> List[Tuple[str, float]]:
    """Return the top N signals driving the prediction (by absolute contribution)."""
    sorted_c = sorted(contrib.items(), key=lambda x: abs(x[1]), reverse=True)
    return sorted_c[:n]
