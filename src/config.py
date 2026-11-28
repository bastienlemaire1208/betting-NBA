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
    "ATLANTA": "ATL", "ATLANTA HAWKS": "ATL", "ATL": "ATL",
    "BOSTON": "BOS", "BOSTON CELTICS": "BOS", "BOS": "BOS",
    "BROOKLYN": "BKN", "BROOKLYN NETS": "BKN", "BRK": "BKN", "BKN": "BKN",
    "CHARLOTTE": "CHA", "CHARLOTTE HORNETS": "CHA", "CHO": "CHA", "CHA": "CHA",
    "CHICAGO": "CHI", "CHICAGO BULLS": "CHI", "CHI": "CHI",
    "CLEVELAND": "CLE", "CLEVELAND CAVALIERS": "CLE", "CLE": "CLE",
    "DALLAS": "DAL", "DALLAS MAVERICKS": "DAL", "DAL": "DAL",
    "DENVER": "DEN", "DENVER NUGGETS": "DEN", "DEN": "DEN",
    "DETROIT": "DET", "DETROIT PISTONS": "DET", "DET": "DET",
    "GOLDEN STATE": "GSW", "GOLDEN STATE WARRIORS": "GSW", "GSW": "GSW", "GS": "GSW",
    "HOUSTON": "HOU", "HOUSTON ROCKETS": "HOU", "HOU": "HOU",
    "INDIANA": "IND", "INDIANA PACERS": "IND", "IND": "IND",
    "LA CLIPPERS": "LAC", "LOS ANGELES CLIPPERS": "LAC", "LAC": "LAC",
    "LA LAKERS": "LAL", "LOS ANGELES LAKERS": "LAL", "LAL": "LAL",
    "MEMPHIS": "MEM", "MEMPHIS GRIZZLIES": "MEM", "MEM": "MEM",
    "MIAMI": "MIA", "MIAMI HEAT": "MIA", "MIA": "MIA",
    "MILWAUKEE": "MIL", "MILWAUKEE BUCKS": "MIL", "MIL": "MIL",
    "MINNESOTA": "MIN", "MINNESOTA TIMBERWOLVES": "MIN", "MIN": "MIN",
    "NEW ORLEANS": "NOP", "NEW ORLEANS PELICANS": "NOP", "NOP": "NOP", "NO": "NOP",
    "NEW YORK": "NYK", "NEW YORK KNICKS": "NYK", "NYK": "NYK", "NY": "NYK",
    "OKLA CITY": "OKC", "OKLAHOMA CITY": "OKC", "OKLAHOMA CITY THUNDER": "OKC", "OKC": "OKC",
    "ORLANDO": "ORL", "ORLANDO MAGIC": "ORL", "ORL": "ORL",
    "PHILADELPHIA": "PHI", "PHILADELPHIA 76ERS": "PHI", "PHI": "PHI",
    "PHOENIX": "PHX", "PHOENIX SUNS": "PHX", "PHO": "PHX", "PHX": "PHX",
    "PORTLAND": "POR", "PORTLAND TRAIL BLAZERS": "POR", "POR": "POR",
    "SACRAMENTO": "SAC", "SACRAMENTO KINGS": "SAC", "SAC": "SAC",
    "SAN ANTONIO": "SAS", "SAN ANTONIO SPURS": "SAS", "SAS": "SAS", "SA": "SAS",
    "TORONTO": "TOR", "TORONTO RAPTORS": "TOR", "TOR": "TOR",
    "UTAH": "UTA", "UTAH JAZZ": "UTA", "UTA": "UTA",
    "WASHINGTON": "WAS", "WASHINGTON WIZARDS": "WAS", "WAS": "WAS", "WSH": "WAS",
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
    cleaned = name.strip().upper()
    if not cleaned:
        return ""
    return TEAM_CANONICAL_MAP.get(cleaned, cleaned)
