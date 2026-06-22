"""
Analysis pipeline for football-data.org API format.
"""
import asyncio
from typing import List
import logging
import aiohttp

from data import api_client
from data.database import (
    add_to_watch_list, get_watch_list, update_watch_alert,
    mark_watch_finished, save_prediction, prediction_exists,
)
from analysis.features import MatchContext
from analysis.corners import analyse_pre_match, analyse_live
from analysis.llm import get_llm_analysis, format_alert_message

logger = logging.getLogger(__name__)


async def pre_match_scan(session: aiohttp.ClientSession, fixture: dict, league_name: str) -> bool:
    fixture_id = api_client.get_fixture_id(fixture)
    home, away = api_client.get_fixture_teams(fixture)
    home_id, away_id = home.get("id"), away.get("id")
    league_id = fixture.get("league", {}).get("id", 0)

    if not all([fixture_id, home_id, away_id]):
        return False

    home_profile, away_profile, h2h_profile = await asyncio.gather(
        api_client.build_team_profile(session, home_id, league_id),
        api_client.build_team_profile(session, away_id, league_id),
        api_client.build_h2h_profile(session, home_id, away_id),
    )

    ctx = MatchContext(
        fixture_id=fixture_id,
        home_team=home.get("name", "Home"),
        away_team=away.get("name", "Away"),
        league_id=league_id,
        league_name=league_name,
        match_datetime=api_client.get_fixture_datetime(fixture),
        home_profile=home_profile,
        away_profile=away_profile,
        h2h=h2h_profile,
    )

    pred = analyse_pre_match(ctx)
    if pred is None:
        return False

    add_to_watch_list({
        "fixture_id": fixture_id,
        "league_id": 0,
        "league_name": league_name,
        "home_team": ctx.home_team,
        "away_team": ctx.away_team,
        "match_datetime": ctx.match_datetime,
        "pre_match_expected": pred.projected_final_corners,
    })
    logger.info(
        "Watch list: %s vs %s (proj=%.1f conf=%.2f)",
        ctx.home_team, ctx.away_team, pred.projected_final_corners, pred.confidence,
    )
    return True


async def live_scan(session: aiohttp.ClientSession) -> List[dict]:
    watch_list = get_watch_list()
    if not watch_list:
        return []

    alerts = []
    for watched in watch_list:
        fixture_id = watched["fixture_id"]
        try:
            fixture = await api_client.get_fixture_by_id(session, fixture_id)
            if not fixture:
                continue

            status = api_client.get_fixture_status(fixture)
            if status in ("FINISHED", "AWARDED", "CANCELLED", "POSTPONED"):
                mark_watch_finished(fixture_id)
                continue
            if status not in ("IN_PLAY", "PAUSED"):
                continue

            stats_list = await api_client.get_fixture_statistics(session, fixture_id)
            live_stats = api_client.build_live_stats(fixture, stats_list)
            home, away = api_client.get_fixture_teams(fixture)
            league_id = fixture.get("league", {}).get("id", 0)

            home_profile, away_profile = await asyncio.gather(
                api_client.build_team_profile(session, home.get("id"), league_id),
                api_client.build_team_profile(session, away.get("id"), league_id),
            )

            ctx = MatchContext(
                fixture_id=fixture_id,
                home_team=watched["home_team"],
                away_team=watched["away_team"],
                league_id=0,
                league_name=watched["league_name"],
                match_datetime=watched["match_datetime"],
                home_profile=home_profile,
                away_profile=away_profile,
                live=live_stats,
                last_alert_minute=watched["last_alert_minute"],
                pre_match_expected_corners=watched.get("pre_match_expected", 0),
            )

            pred = analyse_live(ctx)
            if pred is None:
                continue

            if prediction_exists(fixture_id):
                update_watch_alert(fixture_id, live_stats.minute)
                continue

            llm_result = get_llm_analysis(ctx, pred)
            alert_text = format_alert_message(ctx, pred, llm_result)

            save_prediction({
                "fixture_id": fixture_id,
                "league_id": 0,
                "league_name": watched["league_name"],
                "home_team": watched["home_team"],
                "away_team": watched["away_team"],
                "match_datetime": watched["match_datetime"],
                "market": pred.market,
                "prediction": pred.prediction,
                "confidence": pred.confidence,
                "estimated_odds": pred.estimated_odds,
                "stats_snapshot": pred.signals,
                "llm_analysis": llm_result.get("full_analysis", ""),
            })
            update_watch_alert(fixture_id, live_stats.minute)
            alerts.append({"alert_text": alert_text, "fixture_id": fixture_id})

        except Exception as e:
            logger.error("Live scan error fixture %d: %s", fixture_id, e)

    return alerts
