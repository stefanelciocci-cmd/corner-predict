import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/tmp/football_bot.db")

# Prediction thresholds
MIN_CONFIDENCE = 0.52          # minimum confidence to push a live alert
MIN_ODDS = 1.50                # minimum estimated odds
OVER_LINE = 9.5                # Over/Under line for total match corners

# Leagues tracked (API-Football season IDs)
TRACKED_LEAGUES = {
    # Club leagues
    "Premier League":           {"id": 39,  "country": "England"},
    "Championship":             {"id": 40,  "country": "England"},
    "Ligue 1":                  {"id": 61,  "country": "France"},
    "Bundesliga":               {"id": 78,  "country": "Germany"},
    "Serie A":                  {"id": 135, "country": "Italy"},
    "La Liga":                  {"id": 140, "country": "Spain"},
    "Eredivisie":               {"id": 88,  "country": "Netherlands"},
    "Primeira Liga":            {"id": 94,  "country": "Portugal"},
    "Pro League":               {"id": 144, "country": "Belgium"},
    "Eliteserien":              {"id": 103, "country": "Norway"},
    "Scottish Premiership":     {"id": 179, "country": "Scotland"},
    "Champions League":         {"id": 2,   "country": "Europe"},
    "Europa League":            {"id": 3,   "country": "Europe"},
    "FIFA Club World Cup":      {"id": 15,  "country": "World"},
    # International tournaments
    "World Cup":                {"id": 1,   "country": "World"},
    "World Cup Qual. Europe":   {"id": 32,  "country": "Europe"},
    "World Cup Qual. S.America":{"id": 34,  "country": "S. America"},
    "World Cup Qual. Asia":     {"id": 30,  "country": "Asia"},
    "World Cup Qual. CONCACAF": {"id": 31,  "country": "CONCACAF"},
    "World Cup Qual. Africa":   {"id": 29,  "country": "Africa"},
    "Euro Championship":        {"id": 4,   "country": "Europe"},
    "Copa America":             {"id": 9,   "country": "S. America"},
    "UEFA Nations League":      {"id": 5,   "country": "Europe"},
}

# Default season for club leagues
CURRENT_SEASON = 2025

# Some competitions run on different seasons — override per league
LEAGUE_SEASONS = {
    1:   2026,   # World Cup
    37:  2026,   # World Cup Qualification Intercontinental
    34:  2026,   # World Cup Qualification South America
    30:  2026,   # World Cup Qualification Asia
    31:  2026,   # World Cup Qualification CONCACAF
    32:  2024,   # World Cup Qualification Europe
    29:  2023,   # World Cup Qualification Africa
    4:   2024,   # Euro Championship
    960: 2024,   # Euro Qualification
    9:   2024,   # Copa America
    15:  2025,   # FIFA Club World Cup
    490: 2025,   # World Cup U20
    2:   2025,   # Champions League
    3:   2025,   # Europa League
}

# Scheduler times (UTC)
MATCH_SCAN_HOUR = 8       # scan upcoming matches at 8am UTC
RESULT_CHECK_HOUR = 23    # check results at 11pm UTC

# API base
# Use RapidAPI base if key is from RapidAPI (longer keys), otherwise direct
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
