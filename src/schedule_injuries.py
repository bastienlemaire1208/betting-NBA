import re
import logging
from datetime import datetime, timedelta, timezone
from io import StringIO
import requests
import pandas as pd
from .config import normalize_team, CANONICAL_TO_FULL

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _nba_game_date_str(offset_days: int = 0) -> str:
    """Retourne la date NBA (fuseau US Eastern / ET) au format YYYYMMDD."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = datetime.now(timezone.utc) - timedelta(hours=5)
    target = now_et + timedelta(days=offset_days)
    return target.strftime("%Y%m%d")


def _extract_games_for_date(yyyymmdd: str) -> list[tuple[str, str]]:
    """Récupère la liste des (Away_Code, Home_Code) pour une date NBA donnée (YYYYMMDD)."""
    games: list[tuple[str, str]] = []

    # Source 1 : ESPN Scoreboard API publique NBA
    url_espn = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={yyyymmdd}"
    try:
        resp = requests.get(url_espn, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for event in data.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            competitors = competitions[0].get("competitors", [])
            home_code = None
            away_code = None
            for comp in competitors:
                abbr = (
                    comp.get("team", {}).get("abbreviation")
                    or comp.get("team", {}).get("shortDisplayName")
                    or comp.get("team", {}).get("displayName")
                    or ""
                )
                norm = normalize_team(abbr)
                if comp.get("homeAway") == "home":
                    home_code = norm
                elif comp.get("homeAway") == "away":
                    away_code = norm
            if home_code and away_code:
                games.append((away_code, home_code))
        if games:
            return games
    except Exception as exc:
        logger.warning(f"ESPN scoreboard indisponible pour {yyyymmdd} : {exc}")

    # Source 2 : NBA CDN Scoreboard quotidien (aujourd'hui principalement)
    if yyyymmdd == _nba_game_date_str(0):
        try:
            resp = requests.get(
                "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for g in data.get("scoreboard", {}).get("games", []):
                home_code = normalize_team(g.get("homeTeam", {}).get("teamTricode", ""))
                away_code = normalize_team(g.get("awayTeam", {}).get("teamTricode", ""))
                if home_code and away_code:
                    games.append((away_code, home_code))
        except Exception as exc:
            logger.warning(f"NBA CDN scoreboard indisponible : {exc}")

    return games


def fetch_todays_games_with_b2b() -> tuple[pd.DataFrame, dict[str, int]]:
    """
    Récupère les matchs NBA du jour et détecte automatiquement Home_B2B / Away_B2B
    en comparant avec les matchs de la veille.
    Retourne (df_matchs_base, b2b_map).
    """
    today_str = _nba_game_date_str(0)
    yesterday_str = _nba_game_date_str(-1)

    todays_games = _extract_games_for_date(today_str)
    yesterdays_games = _extract_games_for_date(yesterday_str)

    teams_yesterday = set()
    for away_c, home_c in yesterdays_games:
        teams_yesterday.add(away_c)
        teams_yesterday.add(home_c)

    b2b_map: dict[str, int] = {}
    rows = []
    for away_c, home_c in todays_games:
        home_b2b = 1 if home_c in teams_yesterday else 0
        away_b2b = 1 if away_c in teams_yesterday else 0
        b2b_map[home_c] = home_b2b
        b2b_map[away_c] = away_b2b
        rows.append({
            "Match": f"{CANONICAL_TO_FULL.get(away_c, away_c)} @ {CANONICAL_TO_FULL.get(home_c, home_c)}",
            "Home_Team": home_c,
            "Away_Team": away_c,
            "Home_B2B": home_b2b,
            "Away_B2B": away_b2b,
            "Home_Out": "",
            "Away_Out": "",
        })

    return pd.DataFrame(rows), b2b_map


def _clean_player_name(raw_name: str) -> str:
    name = re.sub(r"\s*\([^)]*\)", "", str(raw_name))
    name = re.sub(r"\s+", " ", name).strip(" ,;-")
    return name


def _match_player_to_roster(player_name: str, team_code: str, raw_players_df: pd.DataFrame | None) -> str:
    """Aligne si possible le nom du joueur avec la liste officielle de l'équipe."""
    cleaned = _clean_player_name(player_name)
    if not cleaned or raw_players_df is None or raw_players_df.empty:
        return cleaned

    sub = raw_players_df
    if "Team_Code" in sub.columns and team_code:
        team_sub = sub[sub["Team_Code"] == team_code]
        if not team_sub.empty:
            sub = team_sub

    target_lower = cleaned.lower()
    # Correspondance exacte ou partielle
    for p in sub["Player"].dropna().astype(str).tolist():
        p_low = p.lower()
        if target_lower == p_low or target_lower in p_low or p_low in target_lower:
            return p

    # Correspondance par nom de famille
    parts = [w for w in re.split(r"\W+", target_lower) if len(w) > 2]
    if parts:
        last_word = parts[-1]
        for p in sub["Player"].dropna().astype(str).tolist():
            if re.search(rf"\b{re.escape(last_word)}\b", p.lower()):
                return p

    return cleaned


def _scrape_donbest_out(raw_players_df: pd.DataFrame | None = None) -> dict[str, list[str]]:
    """Tente d'extraire les joueurs OUT depuis DonBest NBA Injuries."""
    out_by_team: dict[str, list[str]] = {}
    url = "https://www.donbest.com/nba/injuries"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # Tentative 1 : tableaux HTML pandas
        try:
            tables = pd.read_html(StringIO(html))
            for tbl in tables:
                cols_low = [str(c).lower() for c in tbl.columns]
                team_col = next((c for c in tbl.columns if any(k in str(c).lower() for k in ["team", "tm"])), None)
                player_col = next((c for c in tbl.columns if any(k in str(c).lower() for k in ["player", "name"])), None)
                status_col = next((c for c in tbl.columns if any(k in str(c).lower() for k in ["status", "injury", "inj"])), None)

                if team_col and player_col:
                    for _, r in tbl.iterrows():
                        t_code = normalize_team(str(r.get(team_col, "")))
                        p_raw = str(r.get(player_col, ""))
                        st_txt = str(r.get(status_col, "")) if status_col else f"{p_raw} {' '.join(str(v) for v in r.values)}"
                        if t_code and re.search(r"\bout\b", st_txt, flags=re.IGNORECASE):
                            p_norm = _match_player_to_roster(p_raw, t_code, raw_players_df)
                            if p_norm:
                                out_by_team.setdefault(t_code, [])
                                if p_norm not in out_by_team[t_code]:
                                    out_by_team[t_code].append(p_norm)
        except Exception:
            pass

        # Tentative 2 : parsing léger du texte / lignes HTML si les tableaux sont imbriqués
        if not out_by_team:
            text_lines = [re.sub(r"\s+", " ", line).strip() for line in re.sub(r"<[^>]+>", "\n", html).splitlines()]
            current_team = None
            for line in text_lines:
                if not line:
                    continue
                t_cand = normalize_team(line)
                if t_cand in CANONICAL_TO_FULL and len(line) <= 35:
                    current_team = t_cand
                    continue
                if current_team and re.search(r"\bout\b", line, flags=re.IGNORECASE):
                    p_part = re.split(r"(?i)\bout\b", line)[0]
                    p_norm = _match_player_to_roster(p_part, current_team, raw_players_df)
                    if p_norm and len(p_norm) > 2:
                        out_by_team.setdefault(current_team, [])
                        if p_norm not in out_by_team[current_team]:
                            out_by_team[current_team].append(p_norm)
    except Exception as exc:
        logger.warning(f"Scraping DonBest injuries indisponible : {exc}")

    return out_by_team


def _scrape_espn_out(raw_players_df: pd.DataFrame | None = None) -> dict[str, list[str]]:
    """Source de secours fiable : rapport de blessures ESPN NBA."""
    out_by_team: dict[str, list[str]] = {}
    url = "https://www.espn.com/nba/injuries"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        for tbl in tables:
            player_col = next((c for c in tbl.columns if str(c).lower() in ["name", "player"]), None)
            status_col = next((c for c in tbl.columns if "status" in str(c).lower()), None)
            if not player_col:
                continue
            for _, r in tbl.iterrows():
                status_val = str(r.get(status_col, "")) if status_col else " ".join(str(v) for v in r.values)
                if re.search(r"\bout\b", status_val, flags=re.IGNORECASE):
                    p_raw = str(r.get(player_col, ""))
                    p_norm = _match_player_to_roster(p_raw, "", raw_players_df)
                    if p_norm and raw_players_df is not None and "Team_Code" in raw_players_df.columns:
                        matches = raw_players_df[raw_players_df["Player"].str.lower() == p_norm.lower()]
                        if not matches.empty:
                            t_code = str(matches["Team_Code"].iloc[0])
                            if t_code in CANONICAL_TO_FULL:
                                out_by_team.setdefault(t_code, [])
                                if p_norm not in out_by_team[t_code]:
                                    out_by_team[t_code].append(p_norm)
    except Exception as exc:
        logger.warning(f"Scraping ESPN injuries indisponible : {exc}")
    return out_by_team


def fetch_out_players_by_team(raw_players_df: pd.DataFrame | None = None) -> tuple[dict[str, str], str]:
    """
    Récupère les joueurs OUT par équipe (en privilégiant DonBest, avec complément ESPN).
    Retourne ({team_code: "Joueur 1; Joueur 2"}, source_utilisee).
    """
    donbest_map = _scrape_donbest_out(raw_players_df)
    espn_map = _scrape_espn_out(raw_players_df)

    merged: dict[str, list[str]] = {}
    sources_used = []

    if donbest_map:
        sources_used.append("DonBest")
        for t, players in donbest_map.items():
            merged[t] = list(players)

    if espn_map:
        sources_used.append("ESPN")
        for t, players in espn_map.items():
            merged.setdefault(t, [])
            for p in players:
                if p not in merged[t]:
                    merged[t].append(p)

    formatted = {t: "; ".join(ps) for t, ps in merged.items() if ps}
    source_label = " + ".join(sources_used) if sources_used else "Aucun (saisie manuelle)"
    return formatted, source_label


def build_todays_matchs_df(raw_players_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """
    Construit la table des matchs du jour avec Home_B2B, Away_B2B, Home_Out et Away_Out.
    """
    games_df, _ = fetch_todays_games_with_b2b()
    out_map, source_label = fetch_out_players_by_team(raw_players_df)

    if games_df.empty:
        empty_df = pd.DataFrame(columns=[
            "Match", "Home_Team", "Away_Team", "Home_B2B", "Away_B2B", "Home_Out", "Away_Out"
        ])
        return empty_df, source_label

    games_df["Home_Out"] = games_df["Home_Team"].map(out_map).fillna("")
    games_df["Away_Out"] = games_df["Away_Team"].map(out_map).fillna("")
    return games_df[["Match", "Home_Team", "Away_Team", "Home_B2B", "Away_B2B", "Home_Out", "Away_Out"]], source_label
