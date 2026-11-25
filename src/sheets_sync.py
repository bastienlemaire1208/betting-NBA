import logging
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from .config import GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_PATH

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def _upsert_worksheet(spreadsheet: gspread.Spreadsheet, title: str, df: pd.DataFrame):
    """Crée ou met à jour un onglet Google Sheet avec le contenu d'un DataFrame."""
    clean_df = df.fillna("")
    rows = [clean_df.columns.tolist()] + clean_df.astype(str).values.tolist()

    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=max(len(rows) + 10, 50), cols=max(len(clean_df.columns) + 2, 10))

    ws.update(range_name="A1", values=rows)
    # Mise en forme basique de l'en-tête
    ws.format("A1:Z1", {
        "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.36},
        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
    })
    logger.info(f"Onglet '{title}' synchronisé ({len(clean_df)} lignes).")


def read_slate_from_sheet() -> pd.DataFrame | None:
    """Lit l'onglet 'Matchs_Ce_Soir' depuis Google Sheets si présent."""
    if not GOOGLE_SHEET_ID:
        return None
    client = get_gspread_client()
    sh = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        ws = sh.worksheet("Matchs_Ce_Soir")
        records = ws.get_all_records()
        if records:
            return pd.DataFrame(records)
    except gspread.WorksheetNotFound:
        # Initialise un modèle vierge pour que l'utilisateur puisse saisir ses cotes PS3838
        template = pd.DataFrame([{
            "Home_Team": "BOS",
            "Away_Team": "NYK",
            "Home_B2B": 0,
            "Away_B2B": 0,
            "Home_Out": "",
            "Away_Out": "",
            "PS3838_Total": 224.5,
            "Odds_Over": 1.93,
            "Odds_Under": 1.93,
            "PS3838_Home_Spread": -6.5,
            "Odds_Home_Spread": 1.95,
            "Odds_Away_Spread": 1.91,
        }])
        _upsert_worksheet(sh, "Matchs_Ce_Soir", template)
        return template
    return None


def push_all_to_google_sheets(projections_df: pd.DataFrame, stats_dict: dict[str, pd.DataFrame]):
    """Exporte les projections + tous les onglets de stats vers Google Sheets."""
    if not GOOGLE_SHEET_ID:
        raise ValueError("GOOGLE_SHEET_ID manquant dans le fichier .env")

    client = get_gspread_client()
    sh = client.open_by_key(GOOGLE_SHEET_ID)

    # 1. Onglet principal de décision
    _upsert_worksheet(sh, "Projections_Value", projections_df)

    # 2. Onglets de données brutes et calculées
    tab_mapping = {
        "Team Ratings Table": "Team Ratings Table",
        "Raw Player Stats": "Raw Player Stats",
        "Win % per game": "Win % per game",
        "opponent points per game": "opponent points per game",
        "Points per game": "Points per game",
        "Points per game Seasons": "Points per game Seasons",
        "Standard deviations": "Standard deviations",
        "PTS last 2 weeks": "PTS last 2 weeks",
    }
    if "assists_per_game" in stats_dict:
        tab_mapping["Assists per game"] = "assists_per_game"
    if "rebounds_per_game" in stats_dict:
        tab_mapping["Rebounds per game"] = "rebounds_per_game"

    for sheet_title, dict_key in tab_mapping.items():
        if dict_key in stats_dict:
            _upsert_worksheet(sh, sheet_title, stats_dict[dict_key])
