import time
import logging
from io import StringIO
import requests
import pandas as pd
import numpy as np
from .config import normalize_team, CANONICAL_TO_FULL, INCLUDE_AST_RBD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TEAMRANKINGS_URLS = {
    "Points per game": "https://www.teamrankings.com/nba/stat/points-per-game",
    "opponent points per game": "https://www.teamrankings.com/nba/stat/opponent-points-per-game",
    "Win % per game": "https://www.teamrankings.com/nba/stat/win-pct-all-games",
    "offensive_efficiency": "https://www.teamrankings.com/nba/stat/offensive-efficiency",
    "defensive_efficiency": "https://www.teamrankings.com/nba/stat/defensive-efficiency",
    "possessions_per_game": "https://www.teamrankings.com/nba/stat/possessions-per-game",
}

if INCLUDE_AST_RBD:
    TEAMRANKINGS_URLS["assists_per_game"] = "https://www.teamrankings.com/nba/stat/assists-per-game"
    TEAMRANKINGS_URLS["rebounds_per_game"] = "https://www.teamrankings.com/nba/stat/total-rebounds-per-game"


def _clean_numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace("%", "", regex=False).str.replace("--", "", regex=False).str.strip(),
        errors="coerce",
    )


def fetch_teamrankings_table(url: str) -> pd.DataFrame:
    """Extrait un tableau TeamRankings et normalise la colonne Team."""
    logger.info(f"Scraping TeamRankings : {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    if not tables:
        raise ValueError(f"Aucun tableau trouvé sur {url}")
    df = tables[0].copy()

    # Standardisation des colonnes : Rank, Team, Season, Last 3, Last 1, Home, Away, Prev
    cols = list(df.columns)
    if len(cols) >= 7:
        rename_map = {
            cols[0]: "Rank",
            cols[1]: "Team_Raw",
            cols[2]: "Season",
            cols[3]: "Last_3",
            cols[4]: "Last_1",
            cols[5]: "Home",
            cols[6]: "Away",
        }
        if len(cols) >= 8:
            rename_map[cols[7]] = "Prev_Season"
        df = df.rename(columns=rename_map)

    df["Team"] = df["Team_Raw"].apply(normalize_team)
    df["Team_Name"] = df["Team"].map(CANONICAL_TO_FULL).fillna(df["Team_Raw"])

    for col in ["Season", "Last_3", "Last_1", "Home", "Away", "Prev_Season"]:
        if col in df.columns:
            df[col] = _clean_numeric_series(df[col])
            # Si '--' en début de saison pour Home ou Away, repli sur la moyenne Season
            if col in ["Home", "Away", "Last_3", "Last_1"] and "Season" in df.columns:
                df[col] = df[col].fillna(df["Season"])

    return df


def fetch_bref_player_stats(season_end_year: int = 2026) -> pd.DataFrame:
    """
    Récupère les stats joueurs (onglet 'Raw Player Stats') depuis Basketball-Reference.
    Note : Basketball-Reference utilise l'année de fin de saison (ex: 2026 pour 2025-26).
    """
    url = f"https://www.basketball-reference.com/leagues/NBA_{season_end_year}_per_game.html"
    logger.info(f"Scraping Raw Player Stats : {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0].copy()

    # Supprime les en-têtes répétés toutes les 20 lignes
    df = df[df["Player"] != "Player"].copy()
    # Gestion des joueurs tradés (conserve la ligne '2TM'/'3TM' ou total en priorité)
    df = df.drop_duplicates(subset=["Player"], keep="first")

    team_col = "Team" if "Team" in df.columns else ("Tm" if "Tm" in df.columns else None)
    if team_col:
        df["Team_Code"] = df[team_col].apply(normalize_team)

    numeric_cols = ["Age", "G", "GS", "MP", "FG", "FGA", "FG%", "3P", "3PA", "3P%",
                    "FT", "FTA", "FT%", "ORB", "DRB", "TRB", "AST", "STL", "BLK", "TOV", "PF", "PTS"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    keep_cols = ["Player", "Pos", "Age", "Team_Code", "G", "GS", "MP", "PTS"]
    if INCLUDE_AST_RBD:
        keep_cols.extend(["TRB", "AST"])
    keep_cols = [c for c in keep_cols if c in df.columns]
    return df[keep_cols].reset_index(drop=True)


def fetch_pts_last_2_weeks(raw_players_df: pd.DataFrame) -> pd.DataFrame:
    """
    Récupère la forme récente des joueurs ('PTS last 2 weeks').
    Tente le flux Basketball-Reference Last 14 days / sinon estime via le flux récent.
    """
    url = "https://www.basketball-reference.com/friv/last_n_days.fcgi?n=14&type=per_game"
    try:
        logger.info(f"Scraping PTS last 2 weeks : {url}")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        df = tables[0].copy()
        df = df[df["Player"] != "Player"].copy()
        df["PTS_L14"] = pd.to_numeric(df["PTS"], errors="coerce")
        df["MP_L14"] = pd.to_numeric(df["MP"], errors="coerce")
        team_col = "Tm" if "Tm" in df.columns else "Team"
        df["Team_Code"] = df[team_col].apply(normalize_team)
        return df[["Player", "Team_Code", "MP_L14", "PTS_L14"]].dropna(subset=["PTS_L14"]).reset_index(drop=True)
    except Exception as exc:
        logger.warning(f"Fallback PTS last 2 weeks sur moyenne saison ({exc})")
        fallback = raw_players_df[["Player", "Team_Code", "MP", "PTS"]].copy()
        fallback = fallback.rename(columns={"MP": "MP_L14", "PTS": "PTS_L14"})
        return fallback


def build_team_ratings_table(ppg_df: pd.DataFrame, oppg_df: pd.DataFrame,
                             off_eff_df: pd.DataFrame, def_eff_df: pd.DataFrame) -> pd.DataFrame:
    """Reconstruit l'onglet 'Team Ratings Table' (Adjusted ORtg / DRtg / Net Rating)."""
    base = ppg_df[["Team", "Team_Name", "Season", "Last_3", "Home", "Away"]].rename(
        columns={"Season": "PPG_Season", "Last_3": "PPG_L3", "Home": "PPG_Home", "Away": "PPG_Away"}
    )
    def_sub = oppg_df[["Team", "Season", "Last_3", "Home", "Away"]].rename(
        columns={"Season": "OPPG_Season", "Last_3": "OPPG_L3", "Home": "OPPG_Home", "Away": "OPPG_Away"}
    )
    ortg_sub = off_eff_df[["Team", "Season", "Last_3"]].rename(
        columns={"Season": "ORtg", "Last_3": "ORtg_L3"}
    )
    drtg_sub = def_eff_df[["Team", "Season", "Last_3"]].rename(
        columns={"Season": "DRtg", "Last_3": "DRtg_L3"}
    )

    merged = base.merge(def_sub, on="Team", how="left")
    merged = merged.merge(ortg_sub, on="Team", how="left")
    merged = merged.merge(drtg_sub, on="Team", how="left")
    merged["Net_Rtg"] = (merged["ORtg"] - merged["DRtg"]).round(2)
    merged = merged.sort_values("Net_Rtg", ascending=False).reset_index(drop=True)
    merged.insert(0, "Rk", merged.index + 1)
    return merged


def collect_all_stats(season_end_year: int = 2026,
                      historical_xlsx_path: str | None = None) -> dict[str, pd.DataFrame]:
    """
    Pipeline complet de collecte pour tous les onglets demandés :
    - Team Ratings Table
    - Raw Player Stats
    - Points per game
    - opponent points per game
    - Win % per game
    - Points per game Seasons (historique)
    - Standard deviations
    - PTS last 2 weeks
    - (Optionnel) Assists / Rebounds
    """
    data = {}
    for key, url in TEAMRANKINGS_URLS.items():
        data[key] = fetch_teamrankings_table(url)
        time.sleep(1.0)  # Politesse anti-rate-limit

    data["Team Ratings Table"] = build_team_ratings_table(
        data["Points per game"],
        data["opponent points per game"],
        data["offensive_efficiency"],
        data["defensive_efficiency"],
    )

    # Saisons précédentes ('Points per game Seasons')
    # Utilise la colonne Prev_Season extraite de TeamRankings + archive Excel si fournie
    seasons_df = pd.DataFrame({
        "Team": data["Points per game"]["Team"],
        "Team_Name": data["Points per game"]["Team_Name"],
        "PPG_Current": data["Points per game"]["Season"],
        "PPG_Prev_Season": data["Points per game"].get("Prev_Season", data["Points per game"]["Season"]),
        "OPPG_Current": data["opponent points per game"]["Season"],
        "OPPG_Prev_Season": data["opponent points per game"].get("Prev_Season", data["opponent points per game"]["Season"]),
    })
    if historical_xlsx_path:
        try:
            att_hist = pd.read_excel(historical_xlsx_path, sheet_name="Points per game 2022-2023 (Att)")
            def_hist = pd.read_excel(historical_xlsx_path, sheet_name="Points per game 2022-2023 (Def)")
            att_hist["Team"] = att_hist["Team"].apply(normalize_team)
            def_hist["Team"] = def_hist["Team"].apply(normalize_team)
            seasons_df = seasons_df.merge(
                att_hist[["Team", "2023"]].rename(columns={"2023": "PPG_2022_23"}), on="Team", how="left"
            ).merge(
                def_hist[["Team", "2023"]].rename(columns={"2023": "OPPG_2022_23"}), on="Team", how="left"
            )
        except Exception as exc:
            logger.warning(f"Archive historique Excel non chargée : {exc}")
    data["Points per game Seasons"] = seasons_df

    # Écarts-types ('Standard deviations') calibrés par équipe (Home / Away)
    # Si fichier Excel historique fourni avec données SBR, calcule l'écart-type réel par équipe
    std_df = pd.DataFrame({
        "Team": data["Points per game"]["Team"],
        "Team_Name": data["Points per game"]["Team_Name"],
        "Std_Home": 11.8,
        "Std_Away": 12.1,
    })
    if historical_xlsx_path:
        try:
            sbr = pd.read_excel(historical_xlsx_path, sheet_name="SBR data")
            if {"Team", "VH", "Final"}.issubset(sbr.columns):
                sbr["Team_Code"] = sbr["Team"].apply(normalize_team)
                sbr["Final"] = pd.to_numeric(sbr["Final"], errors="coerce")
                home_std = sbr[sbr["VH"] == "H"].groupby("Team_Code")["Final"].std().rename("Std_Home_Real")
                away_std = sbr[sbr["VH"] == "V"].groupby("Team_Code")["Final"].std().rename("Std_Away_Real")
                std_df = std_df.merge(home_std, left_on="Team", right_index=True, how="left")
                std_df = std_df.merge(away_std, left_on="Team", right_index=True, how="left")
                std_df["Std_Home"] = std_df["Std_Home_Real"].fillna(std_df["Std_Home"]).round(2)
                std_df["Std_Away"] = std_df["Std_Away_Real"].fillna(std_df["Std_Away"]).round(2)
                std_df = std_df[["Team", "Team_Name", "Std_Home", "Std_Away"]]
        except Exception as exc:
            logger.warning(f"Calcul écarts-types SBR ignoré : {exc}")
    data["Standard deviations"] = std_df

    # Stats joueurs & forme sur 14 jours
    raw_players = fetch_bref_player_stats(season_end_year=season_end_year)
    data["Raw Player Stats"] = raw_players
    data["PTS last 2 weeks"] = fetch_pts_last_2_weeks(raw_players)

    return data
