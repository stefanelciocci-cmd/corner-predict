from typing import Optional
"""
API client for API-Football v3 (api-sports.io).
Provides real corner stats, shots, possession, live data.
Free tier: 100 requests/day.
"""
import aiohttp
import asyncio
import logging
from datetime import date, timedelta
from analysis.features import TeamProfile, LiveStats, H2HProfile
from config import API_FOOTBALL_KEY, API_FOOTBALL_BASE, CURRENT_SEASON, LEAGUE_SEASONS, OVER_LINE
from data.database import get_cached_team_profile, upsert_team_stats

logger = logging.getLogger(__name__)
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

_request_count = 0
MAX_DAILY_REQUESTS = 95


async def _get(session: aiohttp.ClientSession, endpoint: str, params: dict = None, _retry: bool = True):
    global _request_count
    if _request_count >= MAX_DAILY_REQUESTS:
        raise RuntimeError("Daily API request limit reached (95)")
    url = f"{API_FOOTBALL_BASE}/{endpoint}"
    async with session.get(url, headers=HEADERS, params=params or {}) as resp:
        _request_count += 1
        if resp.status == 429:
            logger.warning("Rate limited — waiting 65s then retrying...")
            await asyncio.sleep(65)
            if _retry:
                return await _get(session, endpoint, params, _retry=False)
            return {}
        if resp.status != 200:
            logger.error("API %s → %d", endpoint, resp.status)
            return {}
        data = await resp.json()
        errors = data.get("errors", {})
        if errors:
            # Rate limit can also come back as a 200 with errors dict
            err_str = str(errors)
            if "rateLimit" in err_str:
                logger.warning("Rate limit in response — waiting 65s then retrying...")
                await asyncio.sleep(65)
                if _retry:
                    return await _get(session, endpoint, params, _retry=False)
            else:
                logger.error("API errors: %s", errors)
            return {}
        return data.get("response", [])


# ── Fixtures ────────────────────────────────────────────────────────────────

def _season(league_id: int) -> int:
    return LEAGUE_SEASONS.get(league_id, CURRENT_SEASON)


async def get_fixtures_today(session, league_id: int) -> list:
    r = await _get(session, "fixtures", {
        "league": league_id, "season": _season(league_id),
        "date": date.today().isoformat(), "timezone": "UTC",
    })
    return r if isinstance(r, list) else []


async def get_fixtures_tomorrow(session, league_id: int) -> list:
    r = await _get(session, "fixtures", {
        "league": league_id, "season": _season(league_id),
        "date": (date.today() + timedelta(days=1)).isoformat(), "timezone": "UTC",
    })
    return r if isinstance(r, list) else []


async def get_fixture_by_id(session, fixture_id: int) -> Optional[dict]:
    r = await _get(session, "fixtures", {"id": fixture_id})
    return r[0] if isinstance(r, list) and r else None


async def get_fixture_statistics(session, fixture_id: int) -> list:
    r = await _get(session, "fixtures/statistics", {"fixture": fixture_id})
    return r if isinstance(r, list) else []


async def get_leagues_fixtures(session, league_ids: list, for_tomorrow=True) -> list:
    all_fixtures = []
    fn = get_fixtures_tomorrow if for_tomorrow else get_fixtures_today
    for lid in league_ids:
        try:
            fixtures = await fn(session, lid)
            all_fixtures.extend(fixtures)
            await asyncio.sleep(7)  # stay within 10 req/min rate limit
        except RuntimeError as e:
            logger.warning("%s", e)
            break
        except Exception as e:
            logger.error("League %s: %s", lid, e)
    return all_fixtures


# ── Team profile ────────────────────────────────────────────────────────────

async def build_team_profile(session, team_id: int, league_id: int) -> TeamProfile:
    """Build a TeamProfile from the team's last 15 finished fixtures.

    Avoids the blocked `teams/statistics` endpoint by fetching individual
    fixture statistics — available on free plan for any season/competition.
    Results are cached in SQLite for 24 hours to conserve the 100 req/day limit.
    """
    season = _season(league_id)
    cached = get_cached_team_profile(team_id, league_id, season)
    if cached:
        profile = TeamProfile(team_id=team_id)
        profile.team_name = cached["team_name"] or ""
        profile.matches_played = cached["matches_played"] or 0
        profile.avg_corners_for = cached["avg_corners_for"] or 0.0
        profile.avg_corners_against = cached["avg_corners_against"] or 0.0
        extra = {}
        try:
            import json as _json
            extra = _json.loads(cached["extra_json"] or "{}")
        except Exception:
            pass
        profile.avg_shots_total = extra.get("avg_shots_total", 0.0)
        profile.avg_shots_on_target = extra.get("avg_shots_on_target", 0.0)
        profile.avg_crosses = extra.get("avg_crosses", 0.0)
        profile.avg_possession = extra.get("avg_possession", 50.0)
        profile.avg_fouls_committed = extra.get("avg_fouls_committed", 0.0)
        profile.avg_dangerous_attacks = extra.get("avg_dangerous_attacks", 0.0)
        profile.avg_attacks = extra.get("avg_attacks", 0.0)
        profile.crossing_index = extra.get("crossing_index", 0.0)
        profile.direct_play_index = extra.get("direct_play_index", 0.5)
        logger.debug("Team %d profile served from cache", team_id)
        return profile

    fixtures_raw = await _get(session, "fixtures", {
        "team": team_id, "season": _season(league_id), "status": "FT",
    })
    fixtures_all = fixtures_raw if isinstance(fixtures_raw, list) else []
    # Sort by date descending and take the most recent 15
    fixtures_all.sort(key=lambda f: f.get("fixture", {}).get("date", ""), reverse=True)
    fixtures = fixtures_all[:15]

    profile = TeamProfile(team_id=team_id)
    if not fixtures:
        return profile

    # Try to get team name from first fixture
    for fix in fixtures:
        teams = fix.get("teams", {})
        for side in ("home", "away"):
            t = teams.get(side, {})
            if t.get("id") == team_id and t.get("name"):
                profile.team_name = t["name"]
                break
        if profile.team_name:
            break

    corners_for, corners_against = [], []
    shots_total, shots_on, crosses = [], [], []
    possession, fouls, attacks_d, attacks_t = [], [], [], []

    for fix in fixtures:
        fid = fix.get("fixture", {}).get("id")
        if not fid:
            continue

        stats = await _get(session, "fixtures/statistics", {"fixture": fid})
        if not isinstance(stats, list) or len(stats) < 2:
            await asyncio.sleep(0.3)
            continue

        # Determine which index is this team
        team_index = None
        for idx, ts in enumerate(stats):
            if ts.get("team", {}).get("id") == team_id:
                team_index = idx
                break
        if team_index is None:
            await asyncio.sleep(0.3)
            continue

        opponent_index = 1 - team_index if len(stats) == 2 else None

        def _parse(ts: dict, key: str) -> Optional[float]:
            for s in ts.get("statistics", []):
                if key.lower() in s.get("type", "").lower():
                    return _int(s.get("value"))
            return None

        def _parse_pct(ts: dict, key: str) -> Optional[float]:
            for s in ts.get("statistics", []):
                if key.lower() in s.get("type", "").lower():
                    return _pct(s.get("value"))
            return None

        my = stats[team_index]
        opp = stats[opponent_index] if opponent_index is not None else {}

        c_for = _parse(my, "corner")
        c_against = _parse(opp, "corner") if opp else None
        if c_for is not None:
            corners_for.append(c_for)
        if c_against is not None:
            corners_against.append(c_against)

        s = _parse(my, "total shots")
        if s is not None:
            shots_total.append(s)
        son = _parse(my, "shots on goal")
        if son is not None:
            shots_on.append(son)
        cr = _parse(my, "cross")
        if cr is not None:
            crosses.append(cr)
        pos = _parse_pct(my, "ball possession")
        if pos is not None:
            possession.append(pos)
        f = _parse(my, "fouls")
        if f is not None:
            fouls.append(f)
        da = _parse(my, "dangerous attacks")
        if da is not None:
            attacks_d.append(da)
        at = _parse(my, "attacks")
        if at is not None:
            attacks_t.append(at)

        await asyncio.sleep(0.3)

    n = len(corners_for)
    profile.matches_played = n or 1

    def _avg(lst: list) -> float:
        return round(sum(lst) / len(lst), 2) if lst else 0.0

    profile.avg_corners_for = _avg(corners_for)
    profile.avg_corners_against = _avg(corners_against)
    profile.avg_shots_total = _avg(shots_total)
    profile.avg_shots_on_target = _avg(shots_on)
    profile.avg_crosses = _avg(crosses)
    profile.avg_possession = _avg(possession)
    profile.avg_fouls_committed = _avg(fouls)
    profile.avg_dangerous_attacks = _avg(attacks_d)
    profile.avg_attacks = _avg(attacks_t)

    profile.crossing_index = profile.avg_corners_for / max(profile.avg_shots_total, 1) * 10
    profile.direct_play_index = 0.5  # not available without pass accuracy

    if n > 0:
        upsert_team_stats({
            "team_id": team_id, "league_id": league_id, "season": season,
            "team_name": profile.team_name,
            "avg_corners_for": profile.avg_corners_for,
            "avg_corners_against": profile.avg_corners_against,
            "avg_fh_corners_for": 0.0, "avg_fh_corners_against": 0.0,
            "home_avg_corners": 0.0, "away_avg_corners": 0.0,
            "matches_played": n,
            "avg_shots_total": profile.avg_shots_total,
            "avg_shots_on_target": profile.avg_shots_on_target,
            "avg_crosses": profile.avg_crosses,
            "avg_possession": profile.avg_possession,
            "avg_fouls_committed": profile.avg_fouls_committed,
            "avg_dangerous_attacks": profile.avg_dangerous_attacks,
            "avg_attacks": profile.avg_attacks,
            "crossing_index": profile.crossing_index,
            "direct_play_index": profile.direct_play_index,
        })

    return profile


async def build_h2h_profile(session, home_id: int, away_id: int) -> H2HProfile:
    raw = await _get(session, "fixtures/headtohead", {
        "h2h": f"{home_id}-{away_id}", "last": 10,
    })
    fixtures = raw if isinstance(raw, list) else []
    if not fixtures:
        return H2HProfile()

    profile = H2HProfile()
    corner_totals, shot_totals, foul_totals = [], [], []
    over_count = rich_count = 0

    for f in fixtures:
        fid = f.get("fixture", {}).get("id")
        if not fid:
            continue
        stats = await _get(session, "fixtures/statistics", {"fixture": fid})
        if not isinstance(stats, list):
            continue

        corners = shots = fouls = 0
        for team_stat in stats:
            for s in team_stat.get("statistics", []):
                t = s.get("type", "").lower()
                v = _int(s.get("value"))
                if "corner" in t:
                    corners += v
                elif t == "total shots":
                    shots += v
                elif t == "fouls":
                    fouls += v

        if corners > 0:
            corner_totals.append(corners)
            if corners > OVER_LINE:
                over_count += 1
            if corners >= 11:
                rich_count += 1
        if shots > 0:
            shot_totals.append(shots)
        if fouls > 0:
            foul_totals.append(fouls)
        await asyncio.sleep(0.3)

    n = len(corner_totals)
    if n > 0:
        profile.matches_analysed = n
        profile.avg_total_corners = round(sum(corner_totals) / n, 1)
        profile.over_line_rate = round(over_count / n, 2)
        profile.corner_rich_rate = round(rich_count / n, 2)
    if shot_totals:
        profile.avg_total_shots = round(sum(shot_totals) / len(shot_totals), 1)
    if foul_totals:
        profile.avg_fouls = round(sum(foul_totals) / len(foul_totals), 1)

    profile.typical_pace = (
        "high" if profile.avg_total_corners >= 11
        else "low" if profile.avg_total_corners < 9
        else "medium"
    )
    return profile


def build_live_stats(fixture: dict, statistics: list) -> LiveStats:
    """Parse live fixture + statistics into a LiveStats object."""
    live = LiveStats()

    status = fixture.get("fixture", {}).get("status", {})
    live.minute = int(status.get("elapsed") or 0)
    live.is_first_half = status.get("short") in ("1H", "HT")

    goals = fixture.get("goals", {})
    live.home_score = goals.get("home") or 0
    live.away_score = goals.get("away") or 0

    for idx, team_stat in enumerate(statistics):
        side = "home" if idx == 0 else "away"
        for s in team_stat.get("statistics", []):
            t = s.get("type", "").lower()
            v = _int(s.get("value"))
            pct = _pct(s.get("value"))

            if "corner" in t:
                setattr(live, f"{side}_corners", v)
            elif t == "total shots":
                setattr(live, f"{side}_shots_total", v)
            elif t == "shots on goal":
                setattr(live, f"{side}_shots_on_target", v)
            elif t == "shots off goal":
                setattr(live, f"{side}_shots_off_target", v)
            elif t == "blocked shots":
                setattr(live, f"{side}_blocked_shots", v)
            elif t == "ball possession":
                setattr(live, f"{side}_possession", pct)
            elif t == "fouls":
                setattr(live, f"{side}_fouls", v)
            elif t == "yellow cards":
                setattr(live, f"{side}_yellow_cards", v)
            elif t == "offsides":
                setattr(live, f"{side}_offsides", v)
            elif t == "attacks":
                setattr(live, f"{side}_attacks", v)
            elif t == "dangerous attacks":
                setattr(live, f"{side}_dangerous_attacks", v)
            elif "cross" in t:
                setattr(live, f"{side}_crosses", v)

    return live


# ── Helpers ──────────────────────────────────────────────────────────────────

def _int(v) -> int:
    try:
        return int(str(v).replace("%", "").strip()) if v is not None else 0
    except (ValueError, TypeError):
        return 0


def _pct(v) -> float:
    try:
        return float(str(v).replace("%", "").strip()) if v is not None else 50.0
    except (ValueError, TypeError):
        return 50.0


def get_fixture_id(fixture: dict) -> int:
    return fixture.get("fixture", {}).get("id", 0)


def get_fixture_teams(fixture: dict) -> tuple:
    teams = fixture.get("teams", {})
    return teams.get("home", {}), teams.get("away", {})


def get_fixture_datetime(fixture: dict) -> str:
    return fixture.get("fixture", {}).get("date", "")


def get_fixture_status(fixture: dict) -> str:
    return fixture.get("fixture", {}).get("status", {}).get("short", "")


def parse_match_minute(fixture: dict) -> int:
    return int(fixture.get("fixture", {}).get("status", {}).get("elapsed") or 0)
