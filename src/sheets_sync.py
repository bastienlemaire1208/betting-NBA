import logging
import time
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from .config import GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_PATH

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _drop_cols_defg(df: pd.DataFrame) -> pd.DataFrame:
    """Supprime les colonnes D, E, F et G (index 3, 4, 5, 6) de Matchs_Ce_Soir."""
    if df is None or df.empty:
        return df
    cols_to_drop = [df.columns[i] for i in [3, 4, 5, 6] if i < len(df.columns)]
    return df.drop(columns=cols_to_drop).copy()


def _get_streamlit_secrets():
    try:
        import streamlit as st
        return st.secrets
    except Exception:
        return None


def get_gspread_client() -> gspread.Client:
    secrets = _get_streamlit_secrets()
    if secrets is not None and "gcp_service_account" in secrets:
        creds = Credentials.from_service_account_info(
            dict(secrets["gcp_service_account"]),
            scopes=SCOPES,
        )
        return gspread.authorize(creds)

    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet_id() -> str:
    secrets = _get_streamlit_secrets()
    if secrets is not None and "GOOGLE_SHEET_ID" in secrets:
        return str(secrets["GOOGLE_SHEET_ID"])
    return GOOGLE_SHEET_ID


def _upsert_worksheet(spreadsheet: gspread.Spreadsheet, title: str, df: pd.DataFrame):
    """Crée ou met à jour un onglet Google Sheet avec le contenu d'un DataFrame."""
    clean_df = df.fillna("")
    rows = [clean_df.columns.tolist()] + clean_df.astype(str).values.tolist()

    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=title,
            rows=max(len(rows) + 10, 50),
            cols=max(len(clean_df.columns) + 2, 10),
        )

    ws.update(range_name="A1", values=rows)
    ws.format(
        "A1:Z1",
        {
            "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.36},
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
        },
    )
    time.sleep(0.4)
    logger.info(f"Onglet '{title}' synchronisé ({len(clean_df)} lignes).")


def sync_matchs_ce_soir(matchs_df: pd.DataFrame) -> pd.DataFrame:
    """Écrit l'onglet Matchs_Ce_Soir sans les colonnes D, E, F et G."""
    sheet_id = _get_sheet_id()
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID manquant dans Streamlit Secrets ou .env")
    client = get_gspread_client()
    sh = client.open_by_key(sheet_id)
    cleaned = _drop_cols_defg(matchs_df)
    _upsert_worksheet(sh, "Matchs_Ce_Soir", cleaned)
    return cleaned


def read_slate_from_sheet() -> pd.DataFrame | None:
    """Lit l'onglet 'Matchs_Ce_Soir' depuis Google Sheets si présent."""
    sheet_id = _get_sheet_id()
    if not sheet_id:
        return None
    client = get_gspread_client()
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet("Matchs_Ce_Soir")
        records = ws.get_all_records()
        if records:
            return pd.DataFrame(records)
    except gspread.WorksheetNotFound:
        return None
    return None


def inject_signals_into_matchs_ce_soir(spreadsheet: gspread.Spreadsheet, projections_df: pd.DataFrame) -> pd.DataFrame:
    """
    Lit les colonnes K (Signal_Total) et R (Signal_Handicap) dans l'onglet 'Projections_Value'
    et les insère dans 'Matchs_Ce_Soir' (sans les colonnes D, E, F, G).
    """
    sig_total_list: list[str] = []
    sig_spread_list: list[str] = []

    try:
        ws_proj = spreadsheet.worksheet("Projections_Value")
        values = ws_proj.get_all_values()
        if len(values) > 1:
            headers = values[0]
            idx_k = 10 if len(values[1]) > 10 else (headers.index("Signal_Total") if "Signal_Total" in headers else -1)
            idx_r = 17 if len(values[1]) > 17 else (headers.index("Signal_Handicap") if "Signal_Handicap" in headers else -1)
            for row_vals in values[1:]:
                s_tot = row_vals[idx_k] if (idx_k >= 0 and len(row_vals) > idx_k) else ""
                s_spr = row_vals[idx_r] if (idx_r >= 0 and len(row_vals) > idx_r) else ""
                sig_total_list.append(s_tot)
                sig_spread_list.append(s_spr)
    except gspread.WorksheetNotFound:
        pass

    if not sig_total_list and not projections_df.empty:
        sig_total_list = projections_df.get("Signal_Total", pd.Series([""] * len(projections_df))).astype(str).tolist()
        sig_spread_list = projections_df.get("Signal_Handicap", pd.Series([""] * len(projections_df))).astype(str).tolist()

    try:
        ws_matchs = spreadsheet.worksheet("Matchs_Ce_Soir")
        matchs_vals = ws_matchs.get_all_values()
    except gspread.WorksheetNotFound:
        return pd.DataFrame()

    if not matchs_vals:
        return pd.DataFrame()

    m_headers = matchs_vals[0]
    m_rows = matchs_vals[1:]

    # Conserve uniquement les colonnes utiles (A, B, C) avant d'ajouter Signal_Total et Signal_Handicap
    drop_indices = {3, 4, 5, 6} | {i for i, h in enumerate(m_headers) if h in ("Signal_Total", "Signal_Handicap")}
    clean_headers = [h for i, h in enumerate(m_headers) if i not in drop_indices]
    clean_rows = [[val for i, val in enumerate(r) if i not in drop_indices] for r in m_rows]

    num_m = len(clean_rows)
    while len(sig_total_list) < num_m:
        sig_total_list.append("")
    while len(sig_spread_list) < num_m:
        sig_spread_list.append("")

    final_headers = clean_headers + ["Signal_Total", "Signal_Handicap"]
    final_rows = []
    for idx_r, r in enumerate(clean_rows):
        padded = r + [""] * max(0, len(clean_headers) - len(r))
        padded = padded[: len(clean_headers)]
        padded.append(sig_total_list[idx_r] if idx_r < len(sig_total_list) else "")
        padded.append(sig_spread_list[idx_r] if idx_r < len(sig_spread_list) else "")
        final_rows.append(padded)

    ws_matchs.clear()
    all_out = [final_headers] + final_rows
    ws_matchs.update(range_name="A1", values=all_out)
    ws_matchs.format(
        "A1:Z1",
        {
            "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.36},
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
        },
    )
    logger.info("Colonnes Signal_Total (K) et Signal_Handicap (R) insérées dans Matchs_Ce_Soir (sans D, E, F, G).")
    return pd.DataFrame(final_rows, columns=final_headers)


def push_all_to_google_sheets(projections_df: pd.DataFrame, stats_dict: dict[str, pd.DataFrame],
                              matchs_df: pd.DataFrame | None = None) -> pd.DataFrame | None:
    """
    Exporte Projections_Value, met à jour Matchs_Ce_Soir (sans colonnes D, E, F, G),
    puis insère Signal_Total (col K) et Signal_Handicap (col R) dans Matchs_Ce_Soir.
    """
    sheet_id = _get_sheet_id()
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID manquant dans Streamlit Secrets ou .env")

    client = get_gspread_client()
    sh = client.open_by_key(sheet_id)

    if matchs_df is not None and not matchs_df.empty:
        _upsert_worksheet(sh, "Matchs_Ce_Soir", _drop_cols_defg(matchs_df))

    if projections_df is not None and not projections_df.empty:
        _upsert_worksheet(sh, "Projections_Value", projections_df)

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

    return inject_signals_into_matchs_ce_soir(sh, projections_df)
