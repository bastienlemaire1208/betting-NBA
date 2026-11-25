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
    Prend en compte les points sur les 2 dernières semaines + moyenne saison + remplacement banc.
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


def _round_to_half(val: float) -> float:
    """Arrondit une ligne au demi-point le plus proche (format classique PS3838)."""
    return round(val * 2.0) / 2.0


def project_slate(slate_df: pd.DataFrame, stats: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Projette les scores, totaux et handicaps pour la soirée NBA, génère les projections PS3838
    dans Projections_Value et détermine Signal_Total (colonne K) et Signal_Handicap (colonne R).
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

        # 1. Moyenne offensive pondérée
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

        # 3. Projection brute + Net Rating + avantage terrain
        proj_home = (h_off + a_def) / 2.0
        proj_away = (a_off + h_def) / 2.0

        net_diff = (ratings.loc[home, "Net_Rtg"] - ratings.loc[away, "Net_Rtg"]) * 0.08
        proj_home += (HOME_COURT_ADVANTAGE / 2.0) + (net_diff / 2.0)
        proj_away -= (HOME_COURT_ADVANTAGE / 2.0) + (net_diff / 2.0)

        # 4. Ajustement Back-to-Back
        home_b2b = int(row.get("Home_B2B", 0) or 0)
        away_b2b = int(row.get("Away_B2B", 0) or 0)
        if home_b2b:
            proj_home += B2B_PENALTY_OFF
            proj_away += B2B_PENALTY_DEF
        if away_b2b:
            proj_away += B2B_PENALTY_OFF
            proj_home += B2B_PENALTY_DEF

        # 5. Ajustement Absents ('OUT')
        home_out_list = [p for p in str(row.get("Home_Out", "")).split(";") if p.strip()]
        away_out_list = [p for p in str(row.get("Away_Out", "")).split(";") if p.strip()]
        home_inj_pen = calculate_injury_impact(home, home_out_list, raw_players, pts_l2w)
        away_inj_pen = calculate_injury_impact(away, away_out_list, raw_players, pts_l2w)

        proj_home -= home_inj_pen
        proj_away -= away_inj_pen

        proj_total = round(proj_home + proj_away, 2)
        proj_margin_home = round(proj_home - proj_away, 2)  # Positif = victoire domicile
        proj_home_spread = -proj_margin_home            # Négatif = favori à domicile

        # 6. Écarts-types combinés
        std_h = stds.loc[home, "Std_Home"] if home in stds.index else 11.8
        std_a = stds.loc[away, "Std_Away"] if away in stds.index else 12.1
        match_total_std = math.sqrt(std_h**2 + std_a**2)
        if np.isnan(match_total_std) or match_total_std <= 0:
            match_total_std = DEFAULT_TOTAL_STD

        # 7. Projections PS3838 insérées dans Projections_Value (sans import externe de cotes)
        ps_total_proj = _round_to_half(proj_total)
        ps_spread_proj = _round_to_half(proj_home_spread)

        # Cote théorique juste (1/p) autour de la ligne projetée
        prob_over_proj = 1.0 - norm.cdf(ps_total_proj, loc=proj_total, scale=match_total_std)
        prob_under_proj = norm.cdf(ps_total_proj, loc=proj_total, scale=match_total_std)
        theo_odds_over = round(1.0 / max(prob_over_proj, 1e-4), 2)
        theo_odds_under = round(1.0 / max(prob_under_proj, 1e-4), 2)

        prob_home_sp_proj = 1.0 - norm.cdf(-ps_spread_proj, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)
        prob_away_sp_proj = norm.cdf(-ps_spread_proj, loc=proj_margin_home, scale=DEFAULT_MARGIN_STD)
        theo_odds_h_sp = round(1.0 / max(prob_home_sp_proj, 1e-4), 2)
        theo_odds_a_sp = round(1.0 / max(prob_away_sp_proj, 1e-4), 2)

        # Si l'utilisateur a tout de même renseigné une ligne manuelle, on l'utilise ; sinon projection PS3838
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

        # Analyse Over / Under -> Signal_Total (Colonne K : index 10)
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

        # Analyse Handicap -> Signal_Handicap (Colonne R : index 17)
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

        # Ordre exact des colonnes pour que :
        # Colonne K (11e colonne, index 10) = Signal_Total
        # Colonne R (18e colonne, index 17) = Signal_Handicap
        results.append({
            "Match": f"{CANONICAL_TO_FULL.get(away, away)} @ {CANONICAL_TO_FULL.get(home, home)}",  # A (1)
            "Away": away,                                                                          # B (2)
            "Home": home,                                                                          # C (3)
            "Win%_Away": round(win_pct.loc[away, "Season"] * 100, 1),                            # D (4)
            "Win%_Home": round(win_pct.loc[home, "Season"] * 100, 1),                            # E (5)
            "Proj_Away": round(proj_away, 1),                                                      # F (6)
            "Proj_Home": round(proj_home, 1),                                                      # G (7)
            "Proj_Total": proj_total,                                                              # H (8)
            "PS3838_Total": ps_total,                                                              # I (9)
            "Diff_Total": total_edge_pts,                                                          # J (10)
            "Signal_Total": total_signal,                                                          # K (11) -> Colonne K
            "Prob_Total_%": total_prob,                                                            # L (12)
            "EV_Total_%": total_ev,                                                              # M (13)
            "Mise_Total_EUR": total_stake,                                                         # N (14)
            "Proj_Handicap_Home": round(proj_home_spread, 1),                                      # O (15)
            "PS3838_Handicap_Home": ps_spread,                                                     # P (16)
            "Diff_Handicap": spread_edge_pts,                                                      # Q (17)
            "Signal_Handicap": spread_signal,                                                      # R (18) -> Colonne R
            "Prob_Handicap_%": spread_prob,                                                        # S (19)
            "EV_Handicap_%": spread_ev,                                                          # T (20)
            "Mise_Handicap_EUR": spread_stake,                                                     # U (21)
            "Cote_Theo_Over": theo_odds_over,                                                      # V (22)
            "Cote_Theo_Under": theo_odds_under,                                                    # W (23)
            "Cote_Theo_Home_Spread": theo_odds_h_sp,                                               # X (24)
            "Cote_Theo_Away_Spread": theo_odds_a_sp,                                               # Y (25)
            "Malus_Absents_Home": home_inj_pen,                                                    # Z (26)
            "Malus_Absents_Away": away_inj_pen,                                                    # AA (27)
        })

    return pd.DataFrame(results)
