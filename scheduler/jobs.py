"""
Scheduled jobs:
  morning_scan    — 08:00 UTC: scan tomorrow's fixtures, build watch list
  live_poll       — every 5 min: check live matches, push corner alerts
  nightly_resolve — 23:00 UTC: check results, update model weights
"""
import asyncio
import logging
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode

from config import TRACKED_LEAGUES, TELEGRAM_TOKEN
from data import api_client
from data.database import get_active_users, save_prediction, mark_start_notified
from analysis.engine import pre_match_scan, live_scan
from learning.feedback import check_and_resolve

logger = logging.getLogger(__name__)


async def morning_scan():
    """
    Scan today's + tomorrow's fixtures across all leagues.
    Adds high-corner-potential matches to the live watch list.
    Does NOT push alerts — just prepares the watch list.
    """
    logger.info("Morning scan: building watch list...")
    league_ids = [v["id"] for v in TRACKED_LEAGUES.values()]

    async with aiohttp.ClientSession() as session:
        today_fixtures = await api_client.get_leagues_fixtures(session, league_ids, for_tomorrow=False)
        tomorrow_fixtures = await api_client.get_leagues_fixtures(session, league_ids, for_tomorrow=True)
        fixtures = today_fixtures + tomorrow_fixtures
        logger.info("Fetched %d fixtures to analyse", len(fixtures))

        added = 0
        watch_entries = []
        for fixture in fixtures:
            league_id = fixture.get("league", {}).get("id")
            league_name = next(
                (name for name, v in TRACKED_LEAGUES.items() if v["id"] == league_id),
                "Unknown"
            )
            try:
                was_added = await pre_match_scan(session, fixture, league_name)
                if was_added:
                    added += 1
                    home, away = api_client.get_fixture_teams(fixture)
                    kick_off = fixture.get("fixture", {}).get("date", "")[:16].replace("T", " ")
                    watch_entries.append({
                        "home": home.get("name", "?"),
                        "away": away.get("name", "?"),
                        "league": league_name,
                        "time": kick_off,
                    })
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.error("Pre-match scan error for fixture: %s", e)

    logger.info("Morning scan done. %d matches added to watch list.", added)
    await _push_watch_list_summary(watch_entries)


async def live_poll():
    """
    Polls all watched live matches every 5 minutes.
    Sends 'match started' notification when a watched match kicks off.
    Pushes corner alerts when the model fires.
    """
    logger.info("Live poll running...")
    async with aiohttp.ClientSession() as session:
        alerts, start_notifications = await live_scan(session)

    for notif in start_notifications:
        await push_to_users(notif)

    for alert in alerts:
        await push_to_users(alert["alert_text"])
        logger.info("Pushed live alert for fixture %d", alert["fixture_id"])


async def nightly_resolve():
    """Check results for all pending predictions, update weights, notify users."""
    logger.info("Nightly resolve: checking results...")
    async with aiohttp.ClientSession() as session:
        notifications = await check_and_resolve(session)
    for msg in notifications:
        await push_to_users(msg)
    logger.info("Nightly resolve complete. %d results sent.", len(notifications))


async def push_to_users(message: str):
    """Push a message to all active authenticated users."""
    bot = Bot(token=TELEGRAM_TOKEN)
    users = get_active_users()
    if not users:
        return
    for user_row in users:
        try:
            await bot.send_message(
                chat_id=user_row["telegram_id"],
                text=message,
                parse_mode=ParseMode.MARKDOWN,
            )
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning("Push failed for user %d: %s", user_row["telegram_id"], e)


async def _push_watch_list_summary(entries: list):
    """Morning summary: full list of matches being watched today."""
    if not entries:
        msg = (
            "📋 *Today's Watch List*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "No high-potential matches found for today.\n"
            "_I'll keep scanning — check back later or use /scan._"
        )
    else:
        lines = []
        for e in entries:
            lines.append(f"⚽ *{e['home']}* vs *{e['away']}*\n"
                         f"   🏆 {e['league']}  🕐 {e['time']} UTC")
        matches_text = "\n\n".join(lines)
        msg = (
            f"📋 *Today's Watch List — {len(entries)} match(es)*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{matches_text}\n\n"
            f"_I'll notify you when each match kicks off and send a prediction during the game._\n"
            f"_Checkpoints: 25', 35', 45', 55', 65', 75'_"
        )
    await push_to_users(msg)
