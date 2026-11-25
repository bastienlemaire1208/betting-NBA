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


def calculate_injury_impact(team_code: str, out_players: list[str],
                            raw_players: pd.DataFrame, pts_l2w: pd.DataFrame) -> float:
    """
    Estime l'impact net (en points) des joueurs absents ('OUT') pour une équipe.
    Prend en compte les points sur les 2 dernières semaines + moyenne saison + remplacement banc (~45%).
    """
    if not out_players:
        return 0.0

    team_season = raw_players[raw_players["Team_Code"] == team_code].copy()
    team_recent = pts_l2w[pts_l2w["Team_Code"] == team_code].copy()

    total_penalty = 0.0
    for player_name in out_players:
        p_clean = player_name.strip().lower()
        if not p_clean:
            continue

        # Recherche dans PTS last 2 weeks puis fallback Raw Player Stats
        match_l2w = team_recent[team_recent["Player"].str.lower().str.contains(p_clean, na=False)]
        match_ssn = team_season[team_season["Player"].str.lower().str.contains(p_clean, na=False)]

        pts_l14 = float(match_l2w["PTS_L14"].iloc[0]) if not match_l2w.empty else None
        pts_ssn = float(match_ssn["PTS"].iloc[0]) if not match_ssn.empty else None
        mp_ssn = float(match_ssn["MP"].iloc[0]) if not match_ssn.empty else 25.0

        if pts_l14 is not None and pts_ssn is not None:
            player_pts = 0.60 * pts_l14 + 0.40 * pts_ssn
        elif pts_l14 is not None:
            player_pts = pts_l14
        elif pts_ssn is not None:
            player_pts = pts_ssn
        else:
            continue

        # Un remplaçant absorbe environ 45% à 55% du volume de tirs d'un titulaire absent
        replacement_factor = 0.48 if mp_ssn >= 28 else 0.35
        net_loss = player_pts * (1.0 - replacement_factor)
        total_penalty += net_loss

    return round(total_penalty, 2)


def kelly_stake(prob: float, decimal_odds: float, bankroll: float = BANKROLL_EUR,
                fraction: float = KELLY_FRACTION) -> float:
    """Calcule la mise conseillée selon le critère de Kelly fractionné."""
    if decimal_odds <= 1.0 or prob <= 0.0 or prob >= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - prob
    full_kelly = (b * prob - q) / b
    if full_kelly <= 0:
        return 0.0
    return round(bankroll * full_kelly * fraction, 2)


def project_slate(slate_df: pd.DataFrame, stats: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Projette les scores, totaux et handicaps pour la soirée NBA et compare aux lignes PS3838.
    Colonnes attendues dans slate_df :
      - Home_Team, Away_Team
      - Home_B2B (0/1), Away_B2B (0/1)
      - Home_Out (noms séparés par ';'), Away_Out (noms séparés par ';')
      - PS3838_Total, Odds_Over, Odds_Under
      - PS3838_Home_Spread (ex: -5.5), Odds_Home_Spread, Odds_Away_Spread
    """
    ppg = stats["Points per game"].set_index("Team")
    oppg = stats["opponent points per game"].set_index("Team")
    win_pct = stats["Win % per game"].set_index("Team")
    ratings = stats["Team Ratings Table"].set_index("Team")
    seasons = stats["Points per game Seasons"].set_index("Team")
    stds = stats["Standard deviations"].set_index("Team")
    raw_players = stats["Raw Player Stats"]
    pts_l2w = stats["PTS last 2 weeks"]

    league_avg_ppg = ppg["Season"].mean()
    results = []

    for _, row in slate_df.iterrows():
        home = normalize_team(str(row.get("Home_Team", "")))
        away = normalize_team(str(row.get("Away_Team", "")))
        if not home or not away or home not in ppg.index or away not in ppg.index:
            continue

        # 1. Moyenne offensive pondérée (Saison 45%, Split Dom/Ext 30%, Last 3 15%, Saison préc. 10%)
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

        # 2. Moyenne défensive pondérée
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

        # 3. Projection brute (renormalisée par la moyenne de la ligue + avantage terrain)
        proj_home = ((h_off + a_def) / 2.0) * (league_avg_ppg / ((league_avg_ppg + league_avg_ppg) / 2.0))
        proj_away = ((a_off + h_def) / 2.0)

        # Ajustement léger par le Net Rating relatif
        net_diff = (ratings.loc[home, "Net_Rtg"] - ratings.loc[away, "Net_Rtg"]) * 0.08
        proj_home += (HOME_COURT_ADVANTAGE / 2.0) + (net_diff / 2.0)
        proj_away -= (HOME_COURT_ADVANTAGE / 2.0) + (net_diff / 2.0)

        # 4. Ajustement Back-to-Back (fatigue)
        home_b2b = int(row.get("Home_B2B", 0) or 0)
        away_b2b = int(row.get("Away_B2B", 0) or 0)
        if home_b2b:
            proj_home += B2B_PENALTY_OFF
            proj_away += B2B_PENALTY_DEF
        if away_b2b:
            proj_away += B2B_PENALTY_OFF
            proj_home += B2B_PENALTY_DEF

        # 5. Ajustement Absents ('OUT') via Raw Player Stats + PTS last 2 weeks
        home_out_list = [p for p in str(row.get("Home_Out", "")).split(";") if p.strip()]
        away_out_list = [p for p in str(row.get("Away_Out", "")).split(";") if p.strip()]
        home_inj_pen = calculate_injury_impact(home, home_out_list, raw_players, pts_l2w)
        away_inj_pen = calculate_injury_impact(away, away_out_list, raw_players, pts_l2w)

        proj_home -= home_inj_pen
        proj_away -= away_inj_pen

        proj_total = round(proj_home + proj_away, 2)
        proj_margin_home = round(proj_home - proj_away, 2)  # Positif = victoire domicile

        # 6. Écarts-types combinés pour lois normales (équivalent NORM.INV / NORM.DIST)
        std_h = stds.loc[home, "Std_Home"] if home in stds.index else 11.8
        std_a = stds.loc[away, "Std_Away"] if away in stds.index else 12.1
        match_total_std = math.sqrt(std_h**2 + std_a**2)
        if np.isnan(match_total_std) or match_total_std <= 0:
            match_total_std = DEFAULT_TOTAL_STD

        # 7. Comparaison avec les lignes & cotes PS3838
        ps_total = pd.to_numeric(row.get("PS3838_Total"), errors="coerce")
        odds_over = pd.to_numeric(row.get("Odds_Over"), errors="coerce")
        odds_under = pd.to_numeric(row.get("Odds_Under"), errors="coerce")

        ps_spread = pd.to_numeric(row.get("PS3838_Home_Spread"), errors="coerce")
        odds_h_sp = pd.to_numeric(row.get("Odds_Home_Spread"), errors="coerce")
        odds_a_sp = pd.to_numeric(row.get("Odds_Away_Spread"), errors="coerce")

        # Analyse Over / Under
        total_signal = "NO BET"
        total_edge_pts = np.nan
        total_prob = np.nan
        total_ev = np.nan
        total_stake = 0.0

        if not np.isnan(ps_total):
            total_edge_pts = round(proj_total - ps_total, 2)
            prob_over = 1.0 - norm.cdf(ps_total, loc=proj_total, scale=match_total_std)
            prob_under = norm.cdf(ps_total, loc=proj_total, scale=match_total_std)

            if total_edge_pts >= EDGE_THRESHOLD_TOTAL and not np.isnan(odds_over):
                ev_over = prob_over * odds_over - 1.0
                if ev_over >= MIN_EV_THRESHOLD:
                    total_signal = f"OVER {ps_total}"
                    total_prob = round(prob_over * 100, 1)
                    total_ev = round(ev_over * 100, 1)
                    total_stake = kelly_stake(prob_over, odds_over)
            elif total_edge_pts <= -EDGE_THRESHOLD_TOTAL and not np.isnan(odds_under):
                ev_under = prob_under * odds_under - 1.0
                if ev_under >= MIN_EV_THRESHOLD:
                    total_signal = f"UNDER {ps_total}"
                    total_prob = round(prob_under * 100, 1)
                    total_ev = round(ev_under * 100, 1)
                    total_stake = kelly_stake(prob_under, odds_under)

        # Analyse Handicap (Spread) : ex. ps_spread = -5.5 => Home doit gagner de 6+
        spread_signal = "NO BET"
        spread_edge_pts = np.nan
        spread_prob = np.nan
        spread_ev = np.nan
        spread_stake = 0.0

        if not np.isnan(ps_spread):
            # Marge projetée couvrant le handicap : proj_margin_home + ps_spread > 0
            spread_edge_pts = round(proj_margin_home + ps_spread, 2)
            prob_home_cover = 1.0 - norm.cdf(-ps_spread, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)
            prob_away_cover = norm.cdf(-ps_spread, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)

            if spread_edge_pts >= EDGE_THRESHOLD_SPREAD and not np.isnan(odds_h_sp):
                ev_h = prob_home_cover * odds_h_sp - 1.0
                if ev_h >= MIN_EV_THRESHOLD:
                    spread_signal = f"{home} ({ps_spread:+g})"
                    spread_prob = round(prob_home_cover * 100, 1)
                    spread_ev = round(ev_h * 100, 1)
                    spread_stake = kelly_stake(prob_home_cover, odds_h_sp)
            elif spread_edge_pts <= -EDGE_THRESHOLD_SPREAD and not np.isnan(odds_a_sp):
                ev_a = prob_away_cover * odds_a_sp - 1.0
                if ev_a >= MIN_EV_THRESHOLD:
                    spread_signal = f"{away} ({-ps_spread:+g})"
                    spread_prob = round(prob_away_cover * 100, 1)
                    spread_ev = round(ev_a * 100, 1)
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
            "PS3838_Total": ps_total if not np.isnan(ps_total) else "",
            "Diff_Total": total_edge_pts if not np.isnan(total_edge_pts) else "",
            "Signal_Total": total_signal,
            "Prob_Total_%": total_prob if not np.isnan(total_prob) else "",
            "EV_Total_%": total_ev if not np.isnan(total_ev) else "",
            "Mise_Total_EUR": total_stake,
            "Proj_Handicap_Home": round(-proj_margin_home, 1),
            "PS3838_Handicap_Home": ps_spread if not np.isnan(ps_spread) else "",
            "Diff_Handicap": spread_edge_pts if not np.isnan(spread_edge_pts) else "",
            "Signal_Handicap": spread_signal,
            "Prob_Handicap_%": spread_prob if not np.isnan(spread_prob) else "",
            "EV_Handicap_%": spread_ev if not np.isnan(spread_ev) else "",
            "Mise_Handicap_EUR": spread_stake,
            "Malus_Absents_Home": home_inj_pen,
            "Malus_Absents_Away": away_inj_pen,
        })

    return pd.DataFrame(results)
