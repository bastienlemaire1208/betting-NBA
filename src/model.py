import re
import math
import pandas as pd
import numpy as np
from scipy.stats import norm
from .config import (
    normalize_team,
    CANONICAL_TO_FULL,
    HOME_COURT_ADVANTAGE,
    B2B_PENALTY_OFF,
    B2B_PENALTY_DEF,
    EDGE_THRESHOLD_TOTAL,
    EDGE_THRESHOLD_SPREAD,
    MIN_EV_THRESHOLD,
    DEFAULT_TOTAL_STD,
    DEFAULT_MARGIN_STD,
    BANKROLL_EUR,
    KELLY_FRACTION,
)


def _find_team_column(df: pd.DataFrame | None) -> str | None:
    if df is None or df.empty:
        return None
    for col in ["Team_Code", "Team", "Tm", "TEAM", "team"]:
        if col in df.columns:
            return col
    return None


def _parse_out_entry(raw_entry: str) -> tuple[str, float, str]:
    """
    Extrait le nom du joueur, son poids (1.0 pour Out, 0.5 pour Questionable)
    et son tag (' [Q 50%]' ou '').
    """
    txt = str(raw_entry).strip()
    is_quest = bool(re.search(r"\[Q\s*50%\]|\(Q\s*50%\)|\bquestionable\b", txt, flags=re.IGNORECASE))
    clean_name = re.sub(
        r"\s*\[Q\s*50%\]|\s*\(Q\s*50%\)|\s*\bquestionable\b",
        "",
        txt,
        flags=re.IGNORECASE,
    ).strip(" ,;-")
    weight = 0.50 if is_quest else 1.0
    tag = " [Q 50%]" if is_quest else ""
    return clean_name, weight, tag


def calculate_injury_breakdown(
    team_code: str,
    out_players: list[str],
    raw_players: pd.DataFrame,
    pts_l2w: pd.DataFrame,
) -> tuple[float, float, str]:
    """
    Calcule pour chaque joueur absent ('Out') ou incertain ('Questionable' à 50 %)
    son impact sur l'attaque, la défense et le handicap.
    """
    if not out_players:
        return 0.0, 0.0, ""

    norm_team = normalize_team(team_code) or team_code
    t_col_ssn = _find_team_column(raw_players)
    t_col_l2w = _find_team_column(pts_l2w)

    team_season = (
        raw_players[raw_players[t_col_ssn].astype(str).map(normalize_team) == norm_team].copy()
        if (raw_players is not None and not raw_players.empty and t_col_ssn)
        else pd.DataFrame()
    )
    if team_season.empty and raw_players is not None and not raw_players.empty:
        team_season = raw_players.copy()

    team_recent = (
        pts_l2w[pts_l2w[t_col_l2w].astype(str).map(normalize_team) == norm_team].copy()
        if (pts_l2w is not None and not pts_l2w.empty and t_col_l2w)
        else pd.DataFrame()
    )
    if team_recent.empty and pts_l2w is not None and not pts_l2w.empty:
        team_recent = pts_l2w.copy()

    player_impacts = []
    for raw_item in out_players:
        p_clean, status_mult, tag = _parse_out_entry(raw_item)
        if not p_clean:
            continue
        p_low = p_clean.lower()

        match_l2w = (
            team_recent[team_recent["Player"].astype(str).str.lower().str.contains(p_low, regex=False, na=False)]
            if not team_recent.empty and "Player" in team_recent.columns
            else pd.DataFrame()
        )
        match_ssn = (
            team_season[team_season["Player"].astype(str).str.lower().str.contains(p_low, regex=False, na=False)]
            if not team_season.empty and "Player" in team_season.columns
            else pd.DataFrame()
        )

        # Repli sur la liste globale des joueurs si le joueur a changé d'équipe ou si l'orthographe diffère
        if match_ssn.empty and raw_players is not None and not raw_players.empty and "Player" in raw_players.columns:
            parts = [w for w in re.split(r"\W+", p_low) if len(w) > 2]
            if parts:
                last_w = parts[-1]
                match_ssn = raw_players[
                    raw_players["Player"].astype(str).str.lower().str.contains(rf"\b{re.escape(last_w)}\b", regex=True, na=False)
                ]

        if match_ssn.empty:
            # Joueur non trouvé dans les stats : malus de secours calibré (2.20 pts Out / 1.10 pts Q 50%)
            att_loss = 1.80 * status_mult
            def_loss = 0.40 * status_mult
            total_hp_impact = att_loss + def_loss
            player_impacts.append({
                "name": f"{p_clean}{tag}",
                "score": total_hp_impact,
                "att": att_loss,
                "def": def_loss,
            })
            continue

        mp = float(match_ssn["MP"].iloc[0]) if "MP" in match_ssn.columns and pd.notna(match_ssn["MP"].iloc[0]) else 24.0
        g = float(match_ssn["G"].iloc[0]) if "G" in match_ssn.columns and pd.notna(match_ssn["G"].iloc[0]) else 20.0
        gs = float(match_ssn["GS"].iloc[0]) if "GS" in match_ssn.columns and pd.notna(match_ssn["GS"].iloc[0]) else 0.0
        pts_ssn = float(match_ssn["PTS"].iloc[0]) if "PTS" in match_ssn.columns and pd.notna(match_ssn["PTS"].iloc[0]) else 12.0
        trb = float(match_ssn["TRB"].iloc[0]) if "TRB" in match_ssn.columns and pd.notna(match_ssn["TRB"].iloc[0]) else 4.0
        ast = float(match_ssn["AST"].iloc[0]) if "AST" in match_ssn.columns and pd.notna(match_ssn["AST"].iloc[0]) else 2.0
        pts_l14 = (
            float(match_l2w["PTS_L14"].iloc[0])
            if not match_l2w.empty and "PTS_L14" in match_l2w.columns and pd.notna(match_l2w["PTS_L14"].iloc[0])
            else pts_ssn
        )

        display_name = str(match_ssn["Player"].iloc[0]) if "Player" in match_ssn.columns else p_clean
        label_name = f"{display_name}{tag}"

        if mp < 10.0 or g < 5.0:
            player_impacts.append({"name": label_name, "score": 0.0, "att": 0.0, "def": 0.0})
            continue

        pts_weighted = 0.60 * pts_l14 + 0.40 * pts_ssn
        perf_score = pts_weighted + 0.35 * ast + 0.15 * trb

        minutes_weight = min(max((mp - 10.0) / 26.0, 0.0), 1.15)
        starter_ratio = min(max(gs / max(g, 1.0), 0.0), 1.0)
        role_mult = 1.0 + 0.15 * starter_ratio

        att_loss = min(perf_score * minutes_weight * role_mult * 0.115, 4.2) * status_mult
        def_loss = min(minutes_weight * role_mult * 0.65, 1.2) * status_mult
        total_hp_impact = att_loss + def_loss

        player_impacts.append({
            "name": label_name,
            "score": total_hp_impact,
            "att": att_loss,
            "def": def_loss,
        })

    player_impacts.sort(key=lambda item: item["score"], reverse=True)
    diminishing_weights = [1.0, 0.80, 0.65]

    total_att = 0.0
    total_def = 0.0
    details = []

    for idx, item in enumerate(player_impacts):
        factor = diminishing_weights[idx] if idx < len(diminishing_weights) else 0.50
        att_val = round(item["att"] * factor, 2)
        def_val = round(item["def"] * factor, 2)
        hp_val = round(att_val + def_val, 2)

        total_att += att_val
        total_def += def_val
        details.append(f"{item['name']} (-{hp_val:.2f} pts)")

    return round(total_att, 2), round(total_def, 2), "; ".join(details)


def calculate_injury_impact(
    team_code: str,
    out_players: list[str],
    raw_players: pd.DataFrame,
    pts_l2w: pd.DataFrame,
) -> float:
    att_val, _, _ = calculate_injury_breakdown(team_code, out_players, raw_players, pts_l2w)
    return att_val


def kelly_stake(
    prob: float,
    decimal_odds: float,
    bankroll: float = BANKROLL_EUR,
    fraction: float = KELLY_FRACTION,
) -> float:
    if decimal_odds <= 1.0 or prob <= 0.0 or prob >= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - prob
    full_kelly = (b * prob - q) / b
    if full_kelly <= 0:
        return 0.0
    return round(bankroll * full_kelly * fraction, 2)


def _round_to_half(val: float) -> float:
    return round(val * 2.0) / 2.0


def project_slate(slate_df: pd.DataFrame, stats: dict[str, pd.DataFrame]) -> pd.DataFrame:
    ppg = stats["Points per game"].set_index("Team")
    oppg = stats["opponent points per game"].set_index("Team")
    win_pct = stats["Win % per game"].set_index("Team")
    ratings = stats["Team Ratings Table"].set_index("Team")
    seasons = stats["Points per game Seasons"].set_index("Team")
    stds = stats["Standard deviations"].set_index("Team")
    raw_players = stats["Raw Player Stats"]
    pts_l2w = stats["PTS last 2 weeks"]

    results = []

    for _, row in slate_df.iterrows():
        home = normalize_team(str(row.get("Home_Team", "")))
        away = normalize_team(str(row.get("Away_Team", "")))
        if not home or not away or home not in ppg.index or away not in ppg.index:
            continue

        h_off = (
            0.45 * ppg.loc[home, "Season"]
            + 0.30 * ppg.loc[home, "Home"]
            + 0.15 * ppg.loc[home, "Last_3"]
            + 0.10 * seasons.loc[home, "PPG_Prev_Season"]
        )
        a_off = (
            0.45 * ppg.loc[away, "Season"]
            + 0.30 * ppg.loc[away, "Away"]
            + 0.15 * ppg.loc[away, "Last_3"]
            + 0.10 * seasons.loc[away, "PPG_Prev_Season"]
        )

        h_def = (
            0.45 * oppg.loc[home, "Season"]
            + 0.30 * oppg.loc[home, "Home"]
            + 0.15 * oppg.loc[home, "Last_3"]
            + 0.10 * seasons.loc[home, "OPPG_Prev_Season"]
        )
        a_def = (
            0.45 * oppg.loc[away, "Season"]
            + 0.30 * oppg.loc[away, "Away"]
            + 0.15 * oppg.loc[away, "Last_3"]
            + 0.10 * seasons.loc[away, "OPPG_Prev_Season"]
        )

        proj_home = (h_off + a_def) / 2.0
        proj_away = (a_off + h_def) / 2.0

        net_diff = (ratings.loc[home, "Net_Rtg"] - ratings.loc[away, "Net_Rtg"]) * 0.08
        proj_home += (HOME_COURT_ADVANTAGE / 2.0) + (net_diff / 2.0)
        proj_away -= (HOME_COURT_ADVANTAGE / 2.0) + (net_diff / 2.0)

        home_b2b = int(row.get("Home_B2B", 0) or 0)
        away_b2b = int(row.get("Away_B2B", 0) or 0)
        if home_b2b:
            proj_home += B2B_PENALTY_OFF
            proj_away += B2B_PENALTY_DEF
        if away_b2b:
            proj_away += B2B_PENALTY_OFF
            proj_home += B2B_PENALTY_DEF

        home_out_list = [p for p in str(row.get("Home_Out", "")).split(";") if p.strip()]
        away_out_list = [p for p in str(row.get("Away_Out", "")).split(";") if p.strip()]

        home_att_pen, home_def_pen, home_hp_detail = calculate_injury_breakdown(
            home, home_out_list, raw_players, pts_l2w
        )
        away_att_pen, away_def_pen, away_hp_detail = calculate_injury_breakdown(
            away, away_out_list, raw_players, pts_l2w
        )

        proj_home = proj_home - home_att_pen + away_def_pen
        proj_away = proj_away - away_att_pen + home_def_pen

        proj_total = round(proj_home + proj_away, 2)
        proj_margin_home = round(proj_home - proj_away, 2)
        proj_home_spread = -proj_margin_home

        std_h = stds.loc[home, "Std_Home"] if home in stds.index else 11.8
        std_a = stds.loc[away, "Std_Away"] if away in stds.index else 12.1
        match_total_std = math.sqrt(std_h**2 + std_a**2)
        if np.isnan(match_total_std) or match_total_std <= 0:
            match_total_std = DEFAULT_TOTAL_STD

        ps_total_proj = _round_to_half(proj_total)
        ps_spread_proj = _round_to_half(proj_home_spread)

        prob_over_proj = 1.0 - norm.cdf(ps_total_proj, loc=proj_total, scale=match_total_std)
        prob_under_proj = norm.cdf(ps_total_proj, loc=proj_total, scale=match_total_std)
        theo_odds_over = round(1.0 / max(prob_over_proj, 1e-4), 2)
        theo_odds_under = round(1.0 / max(prob_under_proj, 1e-4), 2)

        prob_home_sp_proj = 1.0 - norm.cdf(-ps_spread_proj, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)
        prob_away_sp_proj = norm.cdf(-ps_spread_proj, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)
        theo_odds_h_sp = round(1.0 / max(prob_home_sp_proj, 1e-4), 2)
        theo_odds_a_sp = round(1.0 / max(prob_away_sp_proj, 1e-4), 2)

        ps_total_in = pd.to_numeric(row.get("PS3838_Total"), errors="coerce")
        ps_total = float(ps_total_in) if not np.isnan(ps_total_in) else ps_total_proj
        odds_over_in = pd.to_numeric(row.get("Odds_Over"), errors="coerce")
        odds_under_in = pd.to_numeric(row.get("Odds_Under"), errors="coerce")
        odds_over = float(odds_over_in) if not np.isnan(odds_over_in) else theo_odds_over
        odds_under = float(odds_under_in) if not np.isnan(odds_under_in) else theo_odds_under

        ps_spread_in = pd.to_numeric(row.get("PS3838_Home_Spread"), errors="coerce")
        ps_spread = float(ps_spread_in) if not np.isnan(ps_spread_in) else ps_spread_proj
        odds_h_sp_in = pd.to_numeric(row.get("Odds_Home_Spread"), errors="coerce")
        odds_a_sp_in = pd.to_numeric(row.get("Odds_Away_Spread"), errors="coerce")
        odds_h_sp = float(odds_h_sp_in) if not np.isnan(odds_h_sp_in) else theo_odds_h_sp
        odds_a_sp = float(odds_a_sp_in) if not np.isnan(odds_a_sp_in) else theo_odds_a_sp

        total_edge_pts = round(proj_total - ps_total, 2)
        prob_over = 1.0 - norm.cdf(ps_total, loc=proj_total, scale=match_total_std)
        prob_under = norm.cdf(ps_total, loc=proj_total, scale=match_total_std)

        total_signal = "NO BET"
        total_prob = ""
        total_ev = ""
        total_stake = 0.0

        if total_edge_pts >= EDGE_THRESHOLD_TOTAL:
            ev_over = prob_over * odds_over - 1.0
            if ev_over >= MIN_EV_THRESHOLD or np.isnan(odds_over_in):
                total_signal = f"OVER {ps_total:g}"
                total_prob = round(prob_over * 100, 1)
                total_ev = round(max(ev_over, 0.0) * 100, 1)
                total_stake = kelly_stake(prob_over, odds_over)
        elif total_edge_pts <= -EDGE_THRESHOLD_TOTAL:
            ev_under = prob_under * odds_under - 1.0
            if ev_under >= MIN_EV_THRESHOLD or np.isnan(odds_under_in):
                total_signal = f"UNDER {ps_total:g}"
                total_prob = round(prob_under * 100, 1)
                total_ev = round(max(ev_under, 0.0) * 100, 1)
                total_stake = kelly_stake(prob_under, odds_under)

        spread_edge_pts = round(proj_margin_home + ps_spread, 2)
        prob_home_cover = 1.0 - norm.cdf(-ps_spread, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)
        prob_away_cover = norm.cdf(-ps_spread, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)

        spread_signal = "NO BET"
        spread_prob = ""
        spread_ev = ""
        spread_stake = 0.0

        if spread_edge_pts >= EDGE_THRESHOLD_SPREAD:
            ev_h = prob_home_cover * odds_h_sp - 1.0
            if ev_h >= MIN_EV_THRESHOLD or np.isnan(odds_h_sp_in):
                spread_signal = f"{home} ({ps_spread:+g})"
                spread_prob = round(prob_home_cover * 100, 1)
                spread_ev = round(max(ev_h, 0.0) * 100, 1)
                spread_stake = kelly_stake(prob_home_cover, odds_h_sp)
        elif spread_edge_pts <= -EDGE_THRESHOLD_SPREAD:
            ev_a = prob_away_cover * odds_a_sp - 1.0
            if ev_a >= MIN_EV_THRESHOLD or np.isnan(odds_a_sp_in):
                spread_signal = f"{away} ({-ps_spread:+g})"
                spread_prob = round(prob_away_cover * 100, 1)
                spread_ev = round(max(ev_a, 0.0) * 100, 1)
                spread_stake = kelly_stake(prob_away_cover, odds_a_sp)

        results.append({
            "Match": f"{CANONICAL_TO_FULL.get(away, away)} @ {CANONICAL_TO_FULL.get(home, home)}",
            "Away": away,
            "Home": home,
            "Win%_Away": round(win_pct.loc[away, "Season"] * 100, 1),
            "Win%_Home": round(win_pct.loc[home, "Season"] * 100, 1),
            "Proj_Away": round(proj_away, 1),
            "Proj_Home": round(proj_home, 1),
            "Proj_Total": proj_total,
            "PS3838_Total": ps_total,
            "Diff_Total": total_edge_pts,
            "Signal_Total": total_signal,
            "Prob_Total_%": total_prob,
            "EV_Total_%": total_ev,
            "Mise_Total_EUR": total_stake,
            "Proj_Handicap_Home": round(proj_home_spread, 1),
            "PS3838_Handicap_Home": ps_spread,
            "Diff_Handicap": spread_edge_pts,
            "Signal_Handicap": spread_signal,
            "Prob_Handicap_%": spread_prob,
            "EV_Handicap_%": spread_ev,
            "Mise_Handicap_EUR": spread_stake,
            "Cote_Theo_Over": theo_odds_over,
            "Cote_Theo_Under": theo_odds_under,
            "Cote_Theo_Home_Spread": theo_odds_h_sp,
            "Cote_Theo_Away_Spread": theo_odds_a_sp,
            "Malus_Att_Absents_Home": home_att_pen,
            "Malus_Att_Absents_Away": away_att_pen,
            "Malus_Def_Absents_Home": home_def_pen,
            "Malus_Def_Absents_Away": away_def_pen,
            "Impact_Handicap_Absents_Home": home_hp_detail,
            "Impact_Handicap_Absents_Away": away_hp_detail,
        })

    return pd.DataFrame(results)
