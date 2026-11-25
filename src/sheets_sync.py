import logging
import time
from datetime import datetime, timedelta, timezone
import numpy as np
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


def _nba_today_iso() -> str:
    """Retourne la date NBA du jour (fuseau US Eastern) au format YYYY-MM-DD."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = datetime.now(timezone.utc) - timedelta(hours=5)
    return now_et.strftime("%Y-%m-%d")


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

    # Conserve uniquement les colonnes utiles (A, B, C) avant d'ajouter Signal_Total (D) et Signal_Handicap (E)
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
    logger.info("Colonnes Signal_Total (D) et Signal_Handicap (E) insérées dans Matchs_Ce_Soir.")
    return pd.DataFrame(final_rows, columns=final_headers)


def _extract_value_bets_rows(
    matchs_df: pd.DataFrame,
    projections_df: pd.DataFrame | None = None,
    full_matchs_df: pd.DataFrame | None = None,
) -> list[dict[str, str]]:
    """
    Extrait les value bets actifs des colonnes D (Signal_Total) et E (Signal_Handicap)
    de Matchs_Ce_Soir, avec pré-remplissage de Cote_PS3838 si présente.
    """
    if matchs_df is None or matchs_df.empty:
        return []

    date_str = _nba_today_iso()
    rows_out: list[dict[str, str]] = []

    odds_lookup: dict[str, dict[str, str]] = {}
    for src_df in (full_matchs_df, projections_df):
        if src_df is None or src_df.empty or "Match" not in src_df.columns:
            continue
        for _, r in src_df.iterrows():
            m_key = str(r.get("Match", "")).strip()
            if not m_key:
                continue
            entry = odds_lookup.setdefault(m_key, {})
            for col in ("Odds_Over", "Odds_Under", "Odds_Home_Spread", "Odds_Away_Spread"):
                if col in src_df.columns:
                    val = pd.to_numeric(r.get(col), errors="coerce")
                    if not np.isnan(val) and val > 1.0:
                        entry[col] = f"{float(val):.2f}"

    for _, r in matchs_df.iterrows():
        match_label = str(r.iloc[0]).strip() if len(r) > 0 else ""
        home_team = str(r.iloc[1]).strip() if len(r) > 1 else ""
        sig_total = str(r.iloc[3]).strip() if len(r) > 3 else ""
        sig_handicap = str(r.iloc[4]).strip() if len(r) > 4 else ""

        m_odds = odds_lookup.get(match_label, {})

        # Colonne D : Signal_Total
        if sig_total and sig_total.upper() not in ("NO BET", "NAN", "NONE", ""):
            cote_tot = ""
            if sig_total.upper().startswith("OVER"):
                cote_tot = m_odds.get("Odds_Over", "")
            elif sig_total.upper().startswith("UNDER"):
                cote_tot = m_odds.get("Odds_Under", "")
            rows_out.append({
                "Date": date_str,
                "Value_Bet": f"{match_label} — {sig_total}",
                "Cote_PS3838": cote_tot,
                "Valide": "",
            })

        # Colonne E : Signal_Handicap
        if sig_handicap and sig_handicap.upper() not in ("NO BET", "NAN", "NONE", ""):
            if home_team and sig_handicap.upper().startswith(home_team.upper()):
                cote_hp = m_odds.get("Odds_Home_Spread", "")
            else:
                cote_hp = m_odds.get("Odds_Away_Spread", "")
            rows_out.append({
                "Date": date_str,
                "Value_Bet": f"{match_label} — {sig_handicap}",
                "Cote_PS3838": cote_hp,
                "Valide": "",
            })

    return rows_out


def sync_value_bets_history(
    spreadsheet: gspread.Spreadsheet,
    matchs_df: pd.DataFrame,
    projections_df: pd.DataFrame | None = None,
    full_matchs_df: pd.DataFrame | None = None,
    tab_name: str = "Historique_ValueBets",
) -> None:
    """
    Incrémente l'onglet Historique_ValueBets (Date | Value_Bet | Cote_PS3838 | Valide)
    sans dupliquer les paris existants ni écraser les cotes et validations manuelles.
    """
    headers = ["Date", "Value_Bet", "Cote_PS3838", "Valide"]
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=500, cols=4)
        ws.update(range_name="A1:D1", values=[headers])

    existing_values = ws.get_all_values()
    if not existing_values:
        ws.update(range_name="A1:D1", values=[headers])
        existing_rows = []
    else:
        existing_rows = existing_values[1:]

    index_by_key: dict[tuple[str, str], int] = {}
    normalized_rows: list[list[str]] = []
    for row in existing_rows:
        padded = (row + ["", "", "", ""])[:4]
        key = (padded[0].strip(), padded[1].strip())
        if key[0] or key[1]:
            index_by_key[key] = len(normalized_rows)
            normalized_rows.append(padded)

    new_candidates = _extract_value_bets_rows(
        matchs_df=matchs_df,
        projections_df=projections_df,
        full_matchs_df=full_matchs_df,
    )

    changed = False
    for item in new_candidates:
        key = (item["Date"].strip(), item["Value_Bet"].strip())
        if key in index_by_key:
            row_idx = index_by_key[key]
            # Complète la cote uniquement si elle est encore vide et qu'une cote PS3838 existe
            if not normalized_rows[row_idx][2].strip() and item["Cote_PS3838"].strip():
                normalized_rows[row_idx][2] = item["Cote_PS3838"].strip()
                changed = True
        else:
            normalized_rows.append([
                item["Date"],
                item["Value_Bet"],
                item["Cote_PS3838"],
                item["Valide"],
            ])
            index_by_key[key] = len(normalized_rows) - 1
            changed = True

    if changed or not existing_values:
        payload = [headers] + normalized_rows
        ws.clear()
        ws.update(range_name=f"A1:D{len(payload)}", values=payload, value_input_option="USER_ENTERED")
        ws.format(
            "A1:D1",
            {
                "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.36},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
            },
        )
        time.sleep(0.4)
        logger.info(f"Onglet '{tab_name}' mis à jour ({len(normalized_rows)} paris au total).")


def push_all_to_google_sheets(
    projections_df: pd.DataFrame,
    stats_dict: dict[str, pd.DataFrame],
    matchs_df: pd.DataFrame | None = None,
    full_matchs_df: pd.DataFrame | None = None,
) -> pd.DataFrame | None:
    """
    Exporte Projections_Value, met à jour Matchs_Ce_Soir (sans colonnes D, E, F, G),
    insère Signal_Total (col D) et Signal_Handicap (col E) dans Matchs_Ce_Soir,
    puis incrémente Historique_ValueBets.
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

    updated_matchs = inject_signals_into_matchs_ce_soir(sh, projections_df)
    sync_value_bets_history(
        sh,
        matchs_df=updated_matchs,
        projections_df=projections_df,
        full_matchs_df=full_matchs_df,
    )
    return updated_matchs
