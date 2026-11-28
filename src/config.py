import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "service_account.json")
INCLUDE_AST_RBD = os.getenv("INCLUDE_AST_RBD", "false").lower() == "true"
BANKROLL_EUR = float(os.getenv("BANKROLL_EUR", "1000.0"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))

# Paramètres du modèle (calibrables sur votre historique)
HOME_COURT_ADVANTAGE = 2.6       # Avantage terrain moyen NBA (points)
B2B_PENALTY_OFF = -1.8           # Pénalité offensive Back-to-Back
B2B_PENALTY_DEF = 1.2            # Relâchement défensif Back-to-Back (points encaissés en plus)
EDGE_THRESHOLD_TOTAL = 3.5       # Écart minimum (pts) vs ligne PS3838 Over/Under
EDGE_THRESHOLD_SPREAD = 3.0      # Écart minimum (pts) vs ligne PS3838 Handicap
MIN_EV_THRESHOLD = 0.03          # Value minimale (+3% EV) pour valider un pari
DEFAULT_TOTAL_STD = 16.5         # Écart-type par défaut d'un total de match NBA
DEFAULT_MARGIN_STD = 13.8        # Écart-type par défaut d'un écart de score NBA

# Dictionnaire canonique des 30 franchises NBA (mapping TeamRankings / BRef / NBA API / PS3838)
TEAM_CANONICAL_MAP = {
    "Atlanta": "ATL", "Atlanta Hawks": "ATL", "ATL": "ATL",
    "Boston": "BOS", "Boston Celtics": "BOS", "BOS": "BOS",
    "Brooklyn": "BKN", "Brooklyn Nets": "BKN", "BRK": "BKN", "BKN": "BKN",
    "Charlotte": "CHA", "Charlotte Hornets": "CHA", "CHO": "CHA", "CHA": "CHA",
    "Chicago": "CHI", "Chicago Bulls": "CHI", "CHI": "CHI",
    "Cleveland": "CLE", "Cleveland Cavaliers": "CLE", "CLE": "CLE",
    "Dallas": "DAL", "Dallas Mavericks": "DAL", "DAL": "DAL",
    "Denver": "DEN", "Denver Nuggets": "DEN", "DEN": "DEN",
    "Detroit": "DET", "Detroit Pistons": "DET", "DET": "DET",
    "Golden State": "GSW", "Golden State Warriors": "GSW", "GSW": "GSW", "GS": "GSW",
    "Houston": "HOU", "Houston Rockets": "HOU", "HOU": "HOU",
    "Indiana": "IND", "Indiana Pacers": "IND", "IND": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "LAC": "LAC",
    "LA Lakers": "LAL", "Los Angeles Lakers": "LAL", "LAL": "LAL",
    "Memphis": "MEM", "Memphis Grizzlies": "MEM", "MEM": "MEM",
    "Miami": "MIA", "Miami Heat": "MIA", "MIA": "MIA",
    "Milwaukee": "MIL", "Milwaukee Bucks": "MIL", "MIL": "MIL",
    "Minnesota": "MIN", "Minnesota Timberwolves": "MIN", "MIN": "MIN",
    "New Orleans": "NOP", "New Orleans Pelicans": "NOP", "NOP": "NOP", "NO": "NOP",
    "New York": "NYK", "New York Knicks": "NYK", "NYK": "NYK", "NY": "NYK",
    "Okla City": "OKC", "Oklahoma City Thunder": "OKC", "Oklahoma City Thunder": "OKC", "OKC": "OKC",
    "Orlando": "ORL", "Orlando Magic": "ORL", "ORL": "ORL",
    "Philadelphia": "PHI", "Philadelphia 76ers": "PHI", "PHI": "PHI",
    "Phoenix": "PHX", "Phoenix Suns": "PHX", "PHO": "PHX", "PHX": "PHX",
    "Portland": "POR", "Portland Trail Blazers": "POR", "POR": "POR",
    "Sacramento": "SAC", "Sacramento Kings": "SAC", "SAC": "SAC",
    "San Antonio": "SAS", "San Antonio Spurs": "SAS", "SAS": "SAS", "SA": "SAS",
    "Toronto": "TOR", "Toronto Raptors": "TOR", "TOR": "TOR",
    "Utah": "UTA", "Utah Jazz": "UTA", "UTA": "UTA",
    "Washington": "WAS", "Washington Wizards": "WAS", "WAS": "WAS",
}

CANONICAL_TO_FULL = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "LA Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}

def normalize_team(name: str) -> str:
    if not isinstance(name, str):
        return ""
    cleaned = name.strip()
    return TEAM_CANONICAL_MAP.get(cleaned, cleaned.upper())
