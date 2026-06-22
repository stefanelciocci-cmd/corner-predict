"""
Feature extraction layer.
Collects every match signal that contributes to corner prediction:
  - Live stats (shots, possession, crosses, fouls, attacks, xG proxy)
  - Historical team style (crossing index, pressing, shot volume)
  - Match context (score, momentum, urgency)
  - H2H tendencies
  - Referee profile
"""
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


# ── Historical team profile ────────────────────────────────────────────────

@dataclass
class TeamProfile:
    team_id: int = 0
    team_name: str = ""
    matches_played: int = 0

    # Corners
    avg_corners_for: float = 0.0
    avg_corners_against: float = 0.0

    # Shooting
    avg_shots_total: float = 0.0
    avg_shots_on_target: float = 0.0
    avg_shots_off_target: float = 0.0
    avg_blocked_shots: float = 0.0      # blocked shots often lead to corners

    # Attacking style
    avg_crosses: float = 0.0            # crosses attempted per game
    avg_attacks: float = 0.0            # total attacks
    avg_dangerous_attacks: float = 0.0

    # Possession & build-up
    avg_possession: float = 50.0        # % possession
    avg_passes: float = 0.0
    avg_pass_accuracy: float = 0.0

    # Defending style
    avg_fouls_committed: float = 0.0
    avg_fouls_suffered: float = 0.0
    avg_yellow_cards: float = 0.0
    avg_offsides: float = 0.0

    # Home/away splits
    home_avg_corners: float = 0.0
    away_avg_corners: float = 0.0
    home_avg_shots: float = 0.0
    away_avg_shots: float = 0.0

    # Derived style indices (computed, not from API directly)
    crossing_index: float = 0.0         # how wide/crossing-focused the team is
    press_intensity: float = 0.0        # pressing high → more turnovers → more corners
    direct_play_index: float = 0.0      # direct play → more aerial duels → more corners


# ── Live match snapshot ────────────────────────────────────────────────────

@dataclass
class LiveStats:
    """Real-time stats polled from the API every 5 minutes."""
    minute: int = 0
    is_first_half: bool = True

    # Score
    home_score: int = 0
    away_score: int = 0

    # Corners
    home_corners: int = 0
    away_corners: int = 0

    # Shots
    home_shots_total: int = 0
    away_shots_total: int = 0
    home_shots_on_target: int = 0
    away_shots_on_target: int = 0
    home_shots_off_target: int = 0
    away_shots_off_target: int = 0
    home_blocked_shots: int = 0
    away_blocked_shots: int = 0

    # Possession
    home_possession: float = 50.0
    away_possession: float = 50.0

    # Crosses & attacks
    home_crosses: int = 0
    away_crosses: int = 0
    home_attacks: int = 0
    away_attacks: int = 0
    home_dangerous_attacks: int = 0
    away_dangerous_attacks: int = 0

    # Fouls & cards
    home_fouls: int = 0
    away_fouls: int = 0
    home_yellow_cards: int = 0
    away_yellow_cards: int = 0

    # Offsides (pressing proxy)
    home_offsides: int = 0
    away_offsides: int = 0


# ── H2H summary ────────────────────────────────────────────────────────────

@dataclass
class H2HProfile:
    matches_analysed: int = 0
    avg_total_corners: float = 0.0
    over_line_rate: float = 0.0         # % over 9.5 total corners
    avg_total_shots: float = 0.0
    avg_total_possession_home: float = 50.0
    avg_fouls: float = 0.0
    avg_cards: float = 0.0
    corner_rich_rate: float = 0.0       # % with 11+ corners (strong signal)
    typical_pace: str = "medium"        # "high" | "medium" | "low"


# ── Referee profile ────────────────────────────────────────────────────────

@dataclass
class RefereeProfile:
    name: str = ""
    avg_corners_per_game: float = 10.0
    avg_fouls_per_game: float = 22.0
    avg_cards_per_game: float = 3.5
    games_officiated: int = 0


# ── Full match context ─────────────────────────────────────────────────────

@dataclass
class MatchContext:
    fixture_id: int = 0
    home_team: str = ""
    away_team: str = ""
    league_id: int = 0
    league_name: str = ""
    match_datetime: str = ""

    home_profile: TeamProfile = field(default_factory=TeamProfile)
    away_profile: TeamProfile = field(default_factory=TeamProfile)
    h2h: H2HProfile = field(default_factory=H2HProfile)
    referee: RefereeProfile = field(default_factory=RefereeProfile)

    # Set when match is live
    live: Optional[LiveStats] = None
    last_alert_minute: Optional[int] = None
    pre_match_expected_corners: float = 0.0


# ── Derived signals ────────────────────────────────────────────────────────

def compute_derived_signals(ctx: MatchContext) -> dict:
    """
    Compute higher-level signals from raw features.
    These are the inputs to the scoring model.
    """
    hp = ctx.home_profile
    ap = ctx.away_profile
    h2h = ctx.h2h
    ref = ctx.referee
    live = ctx.live

    signals = {}

    # ── Historical style signals ──────────────────────────────────────────

    # Combined attacking output (shots + corners average) — higher = more corners likely
    signals["combined_attack_output"] = (
        (hp.avg_shots_total + ap.avg_shots_total) / 2 +
        (hp.avg_corners_for + ap.avg_corners_for) / 2
    )

    # Crossing intensity — both teams combined
    signals["crossing_intensity"] = (hp.avg_crosses + ap.avg_crosses) / 2

    # Blocking tendency — blocked shots → corners
    signals["block_tendency"] = (hp.avg_blocked_shots + ap.avg_blocked_shots) / 2

    # Foul pressure — teams that foul a lot → more set pieces near box → corners
    signals["foul_pressure"] = (hp.avg_fouls_committed + ap.avg_fouls_committed) / 2

    # Pressing proxy — offsides indicate high press → more transitions → corners
    signals["press_proxy"] = (hp.avg_offsides + ap.avg_offsides) / 2

    # Possession imbalance — lopsided possession → dominated team defends wide → corners
    poss_diff = abs(hp.avg_possession - ap.avg_possession)
    signals["possession_imbalance"] = poss_diff

    # H2H corner richness
    signals["h2h_corner_richness"] = h2h.avg_total_corners
    signals["h2h_over_rate"] = h2h.over_line_rate
    signals["h2h_corner_rich_rate"] = h2h.corner_rich_rate

    # Referee permissiveness (high foul games = more corners)
    signals["referee_corner_avg"] = ref.avg_corners_per_game
    signals["referee_foul_avg"] = ref.avg_fouls_per_game

    # ── Live signals (only if match is running) ───────────────────────────
    if live:
        minute = max(live.minute, 1)
        total_corners = live.home_corners + live.away_corners
        total_shots = live.home_shots_total + live.away_shots_total
        total_crosses = live.home_crosses + live.away_crosses
        total_attacks = live.home_dangerous_attacks + live.away_dangerous_attacks
        total_fouls = live.home_fouls + live.away_fouls
        score_diff = abs(live.home_score - live.away_score)

        # Rates per minute (corner projection foundation)
        signals["live_corner_rate"] = total_corners / minute
        signals["live_shot_rate"] = total_shots / minute
        signals["live_cross_rate"] = total_crosses / minute
        signals["live_attack_rate"] = total_attacks / minute
        signals["live_foul_rate"] = total_fouls / minute

        # Corner efficiency: corners per shot (high = many blocked/saved → corners)
        signals["corner_per_shot"] = total_corners / max(total_shots, 1)

        # Cross-to-corner ratio: how many crosses are converting to corners
        signals["cross_to_corner_ratio"] = total_corners / max(total_crosses, 1)

        # Intensity index: combination of shots + attacks + fouls
        signals["match_intensity"] = (
            signals["live_shot_rate"] * 3 +
            signals["live_attack_rate"] * 1 +
            signals["live_foul_rate"] * 0.5
        )

        # Urgency multiplier: team chasing game attacks wider → corners
        signals["urgency"] = (
            1.20 if score_diff >= 2 else
            1.10 if score_diff == 1 else
            1.00
        )

        # Projected final corners from live pace
        remaining = max(0, 90 - minute)
        sh_boost = 1.22 if not live.is_first_half else 1.0
        signals["projected_corners_live"] = (
            total_corners +
            signals["live_corner_rate"] * remaining * sh_boost * signals["urgency"]
        )

        # Momentum: are corners accelerating? (compare current rate vs first-half rate)
        if not live.is_first_half and minute > 45:
            # second half rate vs first half rate
            fh_implied_rate = (total_corners * 0.45) / 45  # rough
            sh_rate = (total_corners * 0.55) / max(minute - 45, 1)
            signals["corner_momentum"] = sh_rate / max(fh_implied_rate, 0.01)
        else:
            signals["corner_momentum"] = 1.0

        # Shots-to-corners correlation check
        # Typical: ~1 corner per 4-5 shots. If below, corners might catch up.
        expected_corners_from_shots = total_shots / 4.5
        signals["corner_shot_gap"] = expected_corners_from_shots - total_corners

    return signals
