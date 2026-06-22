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
from data.database import get_active_users, save_prediction
from analysis.engine import pre_match_scan, live_scan
from learning.feedback import check_and_resolve

logger = logging.getLogger(__name__)


async def morning_scan():
    """
    Scan tomorrow's fixtures across all leagues.
    Adds high-corner-potential matches to the live watch list.
    Does NOT push alerts — just prepares the watch list.
    """
    logger.info("Morning scan: building watch list for tomorrow...")
    league_ids = [v["id"] for v in TRACKED_LEAGUES.values()]  # codes for football-data.org

    async with aiohttp.ClientSession() as session:
        fixtures = await api_client.get_leagues_fixtures(session, league_ids, for_tomorrow=True)
        logger.info("Fetched %d fixtures to analyse", len(fixtures))

        added = 0
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
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("Pre-match scan error for fixture: %s", e)

    logger.info("Morning scan done. %d matches added to watch list.", added)
    if added > 0:
        await _push_watch_list_summary(added)


async def live_poll():
    """
    Polls all watched live matches every 5 minutes.
    Pushes corner alerts when the model fires.
    """
    logger.debug("Live poll running...")
    async with aiohttp.ClientSession() as session:
        alerts = await live_scan(session)

    for alert in alerts:
        await push_to_users(alert["alert_text"])
        logger.info("Pushed live alert for fixture %d", alert["fixture_id"])


async def nightly_resolve():
    """Check results for all pending predictions and update model weights."""
    logger.info("Nightly resolve: checking results...")
    async with aiohttp.ClientSession() as session:
        await check_and_resolve(session)
    logger.info("Nightly resolve complete.")


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


async def _push_watch_list_summary(count: int):
    """Morning summary: how many matches are being watched today."""
    msg = (
        f"👀 *Today's Watch List Ready*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Tracking *{count}* match(es) for corner betting opportunities.\n"
        f"Live alerts will fire automatically during matches.\n"
        f"_Checkpoints: 25', 35', 45', 55', 65', 75'_"
    )
    await push_to_users(msg)
