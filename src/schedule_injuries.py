import re
import logging
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
from bs4 import BeautifulSoup
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
    """Récupère les matchs NBA du jour et détecte automatiquement Home_B2B / Away_B2B."""
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
    for p in sub["Player"].dropna().astype(str).tolist():
        p_low = p.lower()
        if target_lower == p_low or target_lower in p_low or p_low in target_lower:
            return p

    parts = [w for w in re.split(r"\W+", target_lower) if len(w) > 2]
    if parts:
        last_word = parts[-1]
        for p in sub["Player"].dropna().astype(str).tolist():
            if re.search(rf"\b{re.escape(last_word)}\b", p.lower()):
                return p

    return cleaned


def _scrape_cbs_out(raw_players_df: pd.DataFrame | None = None) -> dict[str, list[str]]:
    """Récupère les joueurs OUT depuis CBS Sports NBA Injuries."""
    out_by_team: dict[str, list[str]] = {}
    url = "https://www.cbssports.com/nba/injuries/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        team_blocks = soup.select("div.TableBase-wrapper, div.Page-colMain section")
        for block in team_blocks:
            title_el = block.find(["h4", "h3", "a", "span"], class_=re.compile(r"TeamName|team", re.I))
            t_code = normalize_team(title_el.get_text(" ", strip=True)) if title_el else ""
            for tr in block.select("tbody tr"):
                row_txt = tr.get_text(" | ", strip=True)
                if re.search(r"\bout\b", row_txt, flags=re.IGNORECASE):
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    p_raw = cells[0] if cells else ""
                    if not p_raw:
                        continue
                    resolved_team = t_code
                    if not resolved_team and raw_players_df is not None and "Team_Code" in raw_players_df.columns:
                        p_guess = _match_player_to_roster(p_raw, "", raw_players_df)
                        m = raw_players_df[raw_players_df["Player"].str.lower() == p_guess.lower()]
                        if not m.empty:
                            resolved_team = str(m["Team_Code"].iloc[0])
                    p_norm = _match_player_to_roster(p_raw, resolved_team, raw_players_df)
                    if resolved_team in CANONICAL_TO_FULL and p_norm:
                        out_by_team.setdefault(resolved_team, [])
                        if p_norm not in out_by_team[resolved_team]:
                            out_by_team[resolved_team].append(p_norm)
    except Exception as exc:
        logger.warning(f"CBS Sports injuries indisponible : {exc}")
    return out_by_team


def _scrape_rotowire_out(raw_players_df: pd.DataFrame | None = None) -> dict[str, list[str]]:
    """Récupère les joueurs OUT publics depuis RotoWire NBA Injury Report."""
    out_by_team: dict[str, list[str]] = {}
    url = "https://www.rotowire.com/basketball/injury-report.php"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select("div.injury-report__player, div.is-injury, tr"):
            txt = item.get_text(" ", strip=True)
            if not re.search(r"\bout\b", txt, flags=re.IGNORECASE):
                continue
            name_el = item.find(["a", "span"], class_=re.compile(r"player|name", re.I))
            team_el = item.find(["span", "div", "td"], class_=re.compile(r"team|abbr", re.I))
            p_raw = name_el.get_text(" ", strip=True) if name_el else ""
            t_code = normalize_team(team_el.get_text(" ", strip=True)) if team_el else ""
            if not t_code and raw_players_df is not None and "Team_Code" in raw_players_df.columns and p_raw:
                p_guess = _match_player_to_roster(p_raw, "", raw_players_df)
                m = raw_players_df[raw_players_df["Player"].str.lower() == p_guess.lower()]
                if not m.empty:
                    t_code = str(m["Team_Code"].iloc[0])
            p_norm = _match_player_to_roster(p_raw, t_code, raw_players_df)
            if t_code in CANONICAL_TO_FULL and p_norm:
                out_by_team.setdefault(t_code, [])
                if p_norm not in out_by_team[t_code]:
                    out_by_team[t_code].append(p_norm)
    except Exception as exc:
        logger.warning(f"RotoWire injuries indisponible : {exc}")
    return out_by_team


def _scrape_donbest_out(raw_players_df: pd.DataFrame | None = None) -> dict[str, list[str]]:
    """Récupère les joueurs OUT depuis DonBest NBA Injuries via BeautifulSoup."""
    out_by_team: dict[str, list[str]] = {}
    url = "https://www.donbest.com/nba/injuries"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        current_team = ""
        for el in soup.find_all(["h2", "h3", "h4", "tr", "li", "div"]):
            txt = " ".join(el.get_text(" ", strip=True).split())
            if not txt or len(txt) > 180:
                continue
            cand_team = normalize_team(txt)
            if cand_team in CANONICAL_TO_FULL and len(txt) <= 35:
                current_team = cand_team
                continue
            if re.search(r"\bout\b", txt, flags=re.IGNORECASE):
                p_part = re.split(r"(?i)\bout\b", txt)[0]
                t_use = current_team
                if not t_use and raw_players_df is not None and "Team_Code" in raw_players_df.columns:
                    p_guess = _match_player_to_roster(p_part, "", raw_players_df)
                    m = raw_players_df[raw_players_df["Player"].str.lower() == p_guess.lower()]
                    if not m.empty:
                        t_use = str(m["Team_Code"].iloc[0])
                p_norm = _match_player_to_roster(p_part, t_use, raw_players_df)
                if t_use in CANONICAL_TO_FULL and p_norm and len(p_norm) > 2:
                    out_by_team.setdefault(t_use, [])
                    if p_norm not in out_by_team[t_use]:
                        out_by_team[t_use].append(p_norm)
    except Exception as exc:
        logger.warning(f"DonBest injuries indisponible : {exc}")
    return out_by_team


def fetch_out_players_by_team(raw_players_df: pd.DataFrame | None = None) -> tuple[dict[str, str], str]:
    """
    Récupère les joueurs OUT par équipe en combinant CBS Sports, RotoWire et DonBest.
    """
    sources = [
        ("CBS Sports", _scrape_cbs_out(raw_players_df)),
        ("RotoWire", _scrape_rotowire_out(raw_players_df)),
        ("DonBest", _scrape_donbest_out(raw_players_df)),
    ]

    merged: dict[str, list[str]] = {}
    sources_used = []

    for label, src_map in sources:
        if src_map:
            sources_used.append(label)
            for t, players in src_map.items():
                merged.setdefault(t, [])
                for p in players:
                    if p not in merged[t]:
                        merged[t].append(p)

    formatted = {t: "; ".join(ps) for t, ps in merged.items() if ps}
    source_label = " + ".join(sources_used) if sources_used else "Aucun (saisie manuelle)"
    return formatted, source_label


def build_todays_matchs_df(raw_players_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """Construit la table des matchs du jour avec Home_B2B, Away_B2B, Home_Out et Away_Out."""
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
