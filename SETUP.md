# Football Prediction Bot — Setup Guide

## 1. Get your API keys

### Telegram Bot Token
1. Open Telegram → search `@BotFather`
2. Send `/newbot` → follow prompts → copy the token

### API-Football (free tier)
1. Go to https://dashboard.api-sports.io/register
2. Register free account → copy your API key
3. Free tier: 100 requests/day — enough for daily scanning

### Anthropic API Key
1. Go to https://console.anthropic.com
2. Create API key → copy it

---

## 2. Local development

```bash
cd football-bot
cp .env.example .env
# Fill in your keys in .env

pip install -r requirements.txt
python -m bot.main
```

---

## 3. Deploy to Fly.io

### Install Fly CLI
```bash
# macOS
brew install flyctl

# or
curl -L https://fly.io/install.sh | sh
```

### Sign up and deploy
```bash
fly auth signup     # or: fly auth login

cd football-bot
fly launch          # creates the app — use existing fly.toml when prompted

# Create persistent volume for SQLite
fly volumes create football_bot_data --region cdg --size 1

# Set secrets (never commit .env to git)
fly secrets set TELEGRAM_TOKEN="your_token_here"
fly secrets set API_FOOTBALL_KEY="your_key_here"
fly secrets set ANTHROPIC_API_KEY="your_key_here"

# Deploy
fly deploy
```

### Check logs
```bash
fly logs
```

---

## 4. Using the bot

1. Open Telegram → find your bot by its username
2. Send `/start` → bot sends you a password
3. Send `/auth <password>` → you're in
4. Wait for predictions — bot scans at 08:00 UTC daily

### Commands
| Command | Description |
|---------|-------------|
| `/start` | Register and get your password |
| `/auth <password>` | Activate your account |
| `/stats` | View bot win rate and history |
| `/help` | Show help |

---

## 5. Fly.io free tier limits

- **3 shared-CPU VMs** free
- **3 GB persistent storage** free
- Bot uses ~1 VM + 1 GB volume → well within free tier

---

## 6. How the learning works

Every night the bot:
1. Fetches results for all pending predictions
2. Checks if Over 4.5 FH corners landed
3. Updates model weights: correct prediction → weight +0.05, wrong → weight -0.03
4. Logs accuracy per league

Over time, features that predict corners well gain higher weights and drive future confidence scores.
