import re
import logging
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
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


class _HtmlTableParser(HTMLParser):
    """Parse les lignes <tr> et cellules <th> / <td> des tableaux HTML."""

    def __init__(self):
        super().__init__()
        self._in_cell = False
        self._buf: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        if t == "tr":
            self._current_row = []
        elif t in ("td", "th"):
            self._in_cell = True
            self._buf = []

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in ("td", "th") and self._in_cell:
            cell_txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._current_row.append(cell_txt)
            self._in_cell = False
            self._buf = []
        elif t == "tr":
            if any(c for c in self._current_row):
                self.rows.append(self._current_row)

    def handle_data(self, data: str):
        if self._in_cell:
            self._buf.append(data)


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
    if not cleaned or raw_players_df is None or raw_players_df.empty or "Player" not in raw_players_df.columns:
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


def _scrape_rotowire_injuries_td(raw_players_df: pd.DataFrame | None = None) -> tuple[dict[str, list[str]], str]:
    """
    Parse les cellules <td> de RotoWire (Player, Team, Pos, Injury, Status, Est. Return).
    Retourne (out_by_team, log_message).
    """
    out_by_team: dict[str, list[str]] = {}
    url = "https://www.rotowire.com/basketball/injury-report.php"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        parser = _HtmlTableParser()
        parser.feed(resp.text)

        player_idx = 0
        team_idx = 1
        status_idx = 4
        matched_rows = 0

        for row in parser.rows:
            low_cells = [c.lower() for c in row]
            if "player" in low_cells and "status" in low_cells:
                player_idx = low_cells.index("player")
                if "team" in low_cells:
                    team_idx = low_cells.index("team")
                status_idx = low_cells.index("status")
                continue

            if len(row) <= max(player_idx, team_idx, status_idx):
                continue

            p_raw = row[player_idx].strip()
            t_raw = row[team_idx].strip()
            st_raw = row[status_idx].strip().lower()
            if not p_raw:
                continue

            is_out = bool(re.search(r"\bout\b", st_raw))
            is_quest = bool(re.search(r"\bquestionable\b", st_raw))
            if not (is_out or is_quest):
                continue

            t_code = normalize_team(t_raw)
            p_norm = _match_player_to_roster(p_raw, t_code, raw_players_df)
            if not t_code or t_code not in CANONICAL_TO_FULL or not p_norm:
                continue

            entry = f"{p_norm} [Q 50%]" if (is_quest and not is_out) else p_norm
            out_by_team.setdefault(t_code, [])
            if entry not in out_by_team[t_code]:
                out_by_team[t_code].append(entry)
                matched_rows += 1

        if matched_rows == 0:
            msg = f"RotoWire HTTP {resp.status_code} mais 0 joueur Out/Questionable détecté (contenu bloqué ou rendu JS)."
            logger.warning(msg)
            return out_by_team, msg

        msg = f"RotoWire OK : {matched_rows} joueur(s) Out/Questionable extrait(s)."
        logger.info(msg)
        return out_by_team, msg

    except Exception as exc:
        msg = f"RotoWire bloqué ou indisponible ({exc})."
        logger.warning(msg)
        return out_by_team, msg


def read_manual_absents_from_sheet(raw_players_df: pd.DataFrame | None = None) -> tuple[dict[str, list[str]], str]:
    """
    Lit l'onglet 'Absents_Manuels' dans Google Sheets (colonnes Team, Player, Status).
    - Status contenant 'questionable' ou '50' -> 'Nom Joueur [Q 50%]'
    - Sinon -> 'Nom Joueur' (Out 100%)
    """
    out_by_team: dict[str, list[str]] = {}
    try:
        from .sheets_sync import get_gspread_client, _get_sheet_id
        sheet_id = _get_sheet_id()
        if not sheet_id:
            return out_by_team, "Absents_Manuels : GOOGLE_SHEET_ID non configuré."

        client = get_gspread_client()
        sh = client.open_by_key(sheet_id)
        ws = sh.worksheet("Absents_Manuels")
        records = ws.get_all_records()

        count = 0
        for rec in records:
            t_raw = str(rec.get("Team") or rec.get("Team_Code") or "").strip()
            p_raw = str(rec.get("Player") or "").strip()
            st_raw = str(rec.get("Status") or "Out").strip().lower()
            t_code = normalize_team(t_raw)
            if not t_code or t_code not in CANONICAL_TO_FULL or not p_raw:
                continue

            p_norm = _match_player_to_roster(p_raw, t_code, raw_players_df)
            is_quest = bool(re.search(r"questionable|50%|q50|q 50", st_raw))
            entry = f"{p_norm} [Q 50%]" if is_quest else p_norm

            out_by_team.setdefault(t_code, [])
            if entry not in out_by_team[t_code]:
                out_by_team[t_code].append(entry)
                count += 1

        msg = f"Absents_Manuels (Google Sheets) : {count} entrée(s) chargée(s)."
        logger.info(msg)
        return out_by_team, msg
    except Exception as exc:
        msg = f"Absents_Manuels indisponible : {exc}"
        logger.warning(msg)
        return out_by_team, msg


def fetch_out_players_by_team(raw_players_df: pd.DataFrame | None = None) -> tuple[dict[str, str], str]:
    """
    Récupère les joueurs OUT (100%) et Questionable (50%).
    Si RotoWire est bloqué ou vide, bascule automatiquement sur l'onglet Google Sheets 'Absents_Manuels'.
    """
    roto_map, roto_log = _scrape_rotowire_injuries_td(raw_players_df)
    manual_map, manual_log = read_manual_absents_from_sheet(raw_players_df)

    merged: dict[str, list[str]] = {}
    sources_used: list[str] = []
    logs: list[str] = [roto_log]

    if roto_map:
        sources_used.append("RotoWire")
        for t, players in roto_map.items():
            merged.setdefault(t, [])
            for p in players:
                if p not in merged[t]:
                    merged[t].append(p)
    else:
        logs.append(manual_log)

    if manual_map:
        sources_used.append("Absents_Manuels")
        for t, players in manual_map.items():
            merged.setdefault(t, [])
            for p in players:
                if p not in merged[t]:
                    merged[t].append(p)

    formatted = {t: "; ".join(ps) for t, ps in merged.items() if ps}
    src_title = " + ".join(sources_used) if sources_used else "Aucun absent détecté"
    full_log = f"{src_title} | {' // '.join(logs)} | Détail={formatted}"
    return formatted, full_log


def build_todays_matchs_df(raw_players_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """Construit la table des matchs du jour avec Home_B2B, Away_B2B, Home_Out et Away_Out."""
    games_df, _ = fetch_todays_games_with_b2b()
    out_map, full_log = fetch_out_players_by_team(raw_players_df)

    if games_df.empty:
        empty_df = pd.DataFrame(columns=[
            "Match", "Home_Team", "Away_Team", "Home_B2B", "Away_B2B", "Home_Out", "Away_Out"
        ])
        return empty_df, full_log

    games_df["Home_Out"] = games_df["Home_Team"].map(out_map).fillna("")
    games_df["Away_Out"] = games_df["Away_Team"].map(out_map).fillna("")
    return games_df[["Match", "Home_Team", "Away_Team", "Home_B2B", "Away_B2B", "Home_Out", "Away_Out"]], full_log
