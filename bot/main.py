import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from config import TELEGRAM_TOKEN, MATCH_SCAN_HOUR, RESULT_CHECK_HOUR
from data.database import init_db
from bot.handlers import (
    cmd_start, cmd_auth, cmd_stats, cmd_help, cmd_scan, unknown_command
)
from scheduler.jobs import morning_scan, live_poll, nightly_resolve

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_app():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("auth", cmd_auth))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    return app


async def main():
    logger.info("Initialising database...")
    init_db()

    logger.info("Starting scheduler...")
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        morning_scan,
        CronTrigger(hour=MATCH_SCAN_HOUR, minute=0),
        id="morning_scan",
        name="Build watch list for today",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        live_poll,
        "interval",
        minutes=5,
        id="live_poll",
        name="Live corner tracking",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        nightly_resolve,
        CronTrigger(hour=RESULT_CHECK_HOUR, minute=0),
        id="nightly_resolve",
        name="Resolve results & learn",
        misfire_grace_time=300,
    )
    scheduler.start()

    logger.info("Starting Telegram bot (polling)...")
    app = build_app()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Run an immediate scan on startup so the watch list is populated right away
    logger.info("Running startup scan...")
    asyncio.ensure_future(morning_scan())

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
