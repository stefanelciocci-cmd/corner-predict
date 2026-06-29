import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.auth import generate_password, hash_password, verify_password
from data.database import (
    upsert_user, activate_user, get_user, touch_user, get_overall_stats
)
from learning.feedback import build_stats_report

logger = logging.getLogger(__name__)


WELCOME_MESSAGE = """⚽ *Welcome to Corner Predictions Bot!*
━━━━━━━━━━━━━━━━━━━━

Hey {name}! 👋 I'm your personal football corners analyst.

*What I do:*
I watch live matches across 20+ leagues — Premier League, Champions League, World Cup, Serie A, Bundesliga and more — and alert you when I spot a high-confidence *Over 9.5 Total Corners* betting opportunity.

*How it works:*
🔍 Every morning I scan upcoming matches and pick the ones with high corner potential based on team stats, crossing style, shot volume and H2H history.

🔴 During those matches I track live corners, shots, possession, dangerous attacks and fouls every 5 minutes — and alert you at the right moment with a projected final corner count.

🤖 Each alert is cross-checked by an AI analyst (Llama 3.3 70B) that reviews all the stats and gives a verdict: *STRONG BET / MODERATE BET / AVOID*.

📈 I track every prediction and learn from my mistakes — my model weights adjust automatically after each result to get better over time.

*Commands:*
• /auth `<password>` — activate your account
• /stats — see my win rate and prediction history
• /help — show this message again

━━━━━━━━━━━━━━━━━━━━
Your access password is:
`{password}`

Send `/auth {password}` to get started!

_Bet responsibly. This is not financial advice._"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tid = user.id
    existing = get_user(tid)

    if existing and existing["is_active"]:
        await update.message.reply_text(
            f"👋 Welcome back, {user.first_name}! You're all set.\n\n"
            "I'll push alerts automatically when I find good corner bets.\n\n"
            "• /stats — view my performance\n"
            "• /help — show all commands",
            parse_mode=ParseMode.MARKDOWN,
        )
        touch_user(tid)
        return

    plain_pw = generate_password()
    hashed_pw = hash_password(plain_pw)
    upsert_user(tid, user.username or "", hashed_pw)

    name = user.first_name or "there"
    await update.message.reply_text(
        WELCOME_MESSAGE.format(name=name, password=plain_pw),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tid = user.id

    if not context.args:
        await update.message.reply_text("Usage: /auth <your_password>")
        return

    provided = context.args[0]
    db_user = get_user(tid)

    if not db_user:
        await update.message.reply_text(
            "Please start with /start to get your password first."
        )
        return

    if not verify_password(provided, db_user["password"]):
        await update.message.reply_text("❌ Incorrect password. Try again.")
        logger.warning("Failed auth attempt for user %d", tid)
        return

    activate_user(tid)
    await update.message.reply_text(
        "✅ *Authentication successful!*\n\n"
        "You'll now receive automatic predictions when the bot finds good corner bets.\n\n"
        "Commands:\n"
        "• /stats — view bot performance\n"
        "• /help — show this help",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("User %d authenticated successfully.", tid)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)

    if not db_user or not db_user["is_active"]:
        await update.message.reply_text(
            "You need to authenticate first. Use /start"
        )
        return

    touch_user(user.id)
    stats = get_overall_stats()
    report = build_stats_report(stats)
    await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ *Football Corner Predictions Bot*\n\n"
        "*What it does:*\n"
        "Analyses upcoming matches and pushes alerts when it finds high-confidence "
        "Over 4.5 First Half Corner bets (estimated odds 1.50+).\n\n"
        "*Commands:*\n"
        "• /start — register and get your password\n"
        "• /auth <password> — activate your account\n"
        "• /scan — scan today's matches now\n"
        "• /stats — bot win rate and history\n"
        "• /help — this message\n\n"
        "*How predictions work:*\n"
        "1. Scans matches daily from 15 European leagues\n"
        "2. Analyses corner stats, H2H, form, referee data\n"
        "3. AI cross-checks the stats\n"
        "4. Only sends when confidence ≥ 65%\n"
        "5. Tracks every result and self-improves\n\n"
        "_Bet responsibly. This is not financial advice._",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_testlive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force-analyze all watched matches right now, ignoring checkpoints and thresholds."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user or not db_user["is_active"]:
        await update.message.reply_text("Authenticate first.")
        return

    import aiohttp
    from data import api_client
    from data.database import get_watch_list
    from analysis.features import MatchContext, compute_derived_signals
    from analysis.scorer import score_signals

    watch = get_watch_list()
    if not watch:
        await update.message.reply_text("Watch list is empty. Run /scan first.")
        return

    await update.message.reply_text(f"🔬 Force-analyzing {len(watch)} watched match(es)...")

    async with aiohttp.ClientSession() as session:
        live_fixtures = await api_client.get_live_fixtures(session)
        live_by_id = {api_client.get_fixture_id(f): f for f in live_fixtures}

        for watched in watch:
            fixture_id = watched["fixture_id"]
            fixture = live_by_id.get(fixture_id) or await api_client.get_fixture_by_id(session, fixture_id)
            if not fixture:
                await update.message.reply_text(f"⚠️ {watched['home_team']} vs {watched['away_team']}: fixture not found")
                continue

            status = api_client.get_fixture_status(fixture)
            stats_list = await api_client.get_fixture_statistics(session, fixture_id)
            live_stats = api_client.build_live_stats(fixture, stats_list)
            home, away = api_client.get_fixture_teams(fixture)
            league_id = fixture.get("league", {}).get("id", 0)

            home_profile = await api_client.build_team_profile(session, home.get("id"), league_id)
            away_profile = await api_client.build_team_profile(session, away.get("id"), league_id)

            ctx = MatchContext(
                fixture_id=fixture_id,
                home_team=watched["home_team"],
                away_team=watched["away_team"],
                league_id=league_id,
                league_name=watched["league_name"],
                match_datetime=watched["match_datetime"],
                home_profile=home_profile,
                away_profile=away_profile,
                live=live_stats,
                last_alert_minute=None,
                pre_match_expected_corners=watched.get("pre_match_expected", 0),
            )

            signals = compute_derived_signals(ctx)
            signals["_matches_played"] = home_profile.matches_played + away_profile.matches_played
            result = score_signals(signals, is_live=True)
            proj = signals.get("projected_corners_live", 0)
            current = (live_stats.home_corners + live_stats.away_corners) if live_stats else 0

            await update.message.reply_text(
                f"📊 *{watched['home_team']} vs {watched['away_team']}*\n"
                f"Status: `{status}` | Minute: {live_stats.minute if live_stats else '?'}\n"
                f"Corners now: {current} | Projected: {proj:.1f}\n"
                f"Confidence: *{result['confidence']*100:.1f}%* (threshold: 52%)\n"
                f"Score: {result['score']:.3f}",
                parse_mode="Markdown"
            )


async def cmd_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger result checking for all pending predictions."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user or not db_user["is_active"]:
        await update.message.reply_text("Authenticate first with /auth")
        return

    from data.database import get_pending_predictions
    pending = get_pending_predictions()
    if not pending:
        await update.message.reply_text(
            "⏳ No pending predictions to resolve yet.\n\n"
            "Predictions are saved when the bot fires a live alert during a match. "
            "Once a match finishes the result will be checked here."
        )
        return

    await update.message.reply_text(f"🔄 Resolving {len(pending)} pending prediction(s)...")
    from scheduler.jobs import nightly_resolve
    import asyncio
    asyncio.ensure_future(nightly_resolve())


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch today's World Cup fixtures raw and report what the API returns."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user or not db_user["is_active"]:
        await update.message.reply_text("Authenticate first with /auth")
        return

    await update.message.reply_text("🔍 Querying API directly...")
    import aiohttp
    from data import api_client
    from datetime import date

    async with aiohttp.ClientSession() as session:
        # Check World Cup today
        wc_today = await api_client.get_fixtures_today(session, 1)
        wc_tomorrow = await api_client.get_fixtures_tomorrow(session, 1)

        lines = [f"*API Debug Report*\n━━━━━━━━━━━━━━━━━━━━"]
        lines.append(f"Today ({date.today()}): *{len(wc_today)}* WC fixtures")
        for f in wc_today[:5]:
            home, away = api_client.get_fixture_teams(f)
            status = api_client.get_fixture_status(f)
            lines.append(f"  • {home.get('name')} vs {away.get('name')} [{status}]")

        lines.append(f"Tomorrow: *{len(wc_tomorrow)}* WC fixtures")
        for f in wc_tomorrow[:5]:
            home, away = api_client.get_fixture_teams(f)
            lines.append(f"  • {home.get('name')} vs {away.get('name')}")

        lines.append(f"\nTotal API requests used: {api_client._request_count}")

    from data.database import get_pending_predictions, get_watch_list
    pending = get_pending_predictions()
    watch = get_watch_list()
    lines.append(f"\n*DB State*")
    lines.append(f"Watch list (active): {len(watch)}")
    lines.append(f"Pending predictions: {len(pending)}")
    for p in pending[:3]:
        lines.append(f"  • {p['home_team']} vs {p['away_team']} — conf {p['confidence']*100:.0f}%")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user or not db_user["is_active"]:
        await update.message.reply_text("You need to authenticate first. Use /start")
        return
    await update.message.reply_text("🔍 Scanning today's matches... I'll notify you when the watch list is ready.")
    from scheduler.jobs import morning_scan
    import asyncio
    asyncio.ensure_future(morning_scan())


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Unknown command. Use /help to see available commands."
    )
