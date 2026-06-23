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
