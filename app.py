import os
import sys
import numpy as np
import pandas as pd
import streamlit as st

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.scraper import collect_all_stats
from src.model import project_slate
from src.schedule_injuries import build_todays_matchs_df
from src.sheets_sync import push_all_to_google_sheets, read_slate_from_sheet

st.set_page_config(
    page_title="NBA Value Betting Engine",
    page_icon="🏀",
    layout="wide",
)

st.title("🏀 NBA Value Betting Engine")
st.caption("Import automatique des matchs, B2B et joueurs OUT • Projections PS3838 • Signaux Value dans Matchs_Ce_Soir")

with st.sidebar:
    st.header("Paramètres")
    season_year = st.number_input(
        "Année de fin de saison NBA",
        min_value=2023,
        max_value=2030,
        value=2027,
        step=1,
        help="Ex. : 2027 pour la saison 2026-27",
    )
    sync_sheets = st.checkbox("Synchroniser avec Google Sheets", value=True)
    run_btn = st.button("🚀 Lancer / Actualiser les projections", type="primary", use_container_width=True)
    force_refresh_btn = st.button("🔄 Vider le cache & relire Absents_Manuels", use_container_width=True)


@st.cache_data(ttl=1800, show_spinner="Collecte des statistiques NBA en cours...")
def cached_collect_stats(end_year: int):
    return collect_all_stats(season_end_year=end_year, historical_xlsx_path=None)


for key, default_val in [
    ("stats", None),
    ("full_matchs_df", None),
    ("matchs_df", None),
    ("out_source", ""),
    ("projections", None),
    ("manual_ps3838_lines", {}),
    ("ps3838_slate_sig", ""),
]:
    if key not in st.session_state:
        st.session_state[key] = default_val


def _reset_pipeline_state() -> None:
    st.cache_data.clear()
    for key in ("stats", "full_matchs_df", "matchs_df", "out_source", "projections"):
        st.session_state[key] = None if key != "out_source" else ""


def _compute_slate_signature(df: pd.DataFrame | None) -> str:
    """Identifie de manière unique la liste des matchs du slate en cours."""
    if df is None or df.empty or "Match" not in df.columns:
        return ""
    matches = [str(m).strip() for m in df["Match"].tolist() if str(m).strip()]
    return "||".join(matches)


def _sync_ps3838_memory_with_slate(df: pd.DataFrame) -> None:
    """Conserve les lignes PS3838 tant que la liste des matchs est identique ; sinon réinitialise."""
    current_sig = _compute_slate_signature(df)
    prev_sig = st.session_state.get("ps3838_slate_sig", "")
    if current_sig and current_sig != prev_sig:
        st.session_state["manual_ps3838_lines"] = {}
    st.session_state["ps3838_slate_sig"] = current_sig


def _apply_saved_ps3838_lines(df: pd.DataFrame) -> pd.DataFrame:
    """Réapplique les lignes PS3838 (Total et Handicap Home) saisies manuellement par match."""
    if df is None or df.empty:
        return df
    _sync_ps3838_memory_with_slate(df)
    out = df.copy()
    if "PS3838_Total" not in out.columns:
        out["PS3838_Total"] = np.nan
    if "PS3838_Home_Spread" not in out.columns:
        out["PS3838_Home_Spread"] = np.nan

    saved = st.session_state.get("manual_ps3838_lines") or {}
    if saved and "Match" in out.columns:
        for idx, r in out.iterrows():
            m_key = str(r.get("Match", "")).strip()
            if m_key in saved:
                if saved[m_key].get("PS3838_Total") is not None:
                    out.at[idx, "PS3838_Total"] = saved[m_key]["PS3838_Total"]
                if saved[m_key].get("PS3838_Home_Spread") is not None:
                    out.at[idx, "PS3838_Home_Spread"] = saved[m_key]["PS3838_Home_Spread"]
    return out


def _drop_cols_defg_app(df: pd.DataFrame) -> pd.DataFrame:
    """Supprime les colonnes D, E, F et G (index 3, 4, 5, 6) pour l'affichage et Matchs_Ce_Soir."""
    if df is None or df.empty:
        return df
    cols_to_drop = [df.columns[i] for i in [3, 4, 5, 6] if i < len(df.columns)]
    return df.drop(columns=cols_to_drop).copy()


def _extract_value_bets_only(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    cols = [
        c for c in ["Match", "Home_Team", "Away_Team", "Signal_Total", "Signal_Handicap"]
        if c in df.columns
    ]
    sub = df[cols].copy()
    has_tot = (
        sub["Signal_Total"].astype(str).str.strip().replace({"": "NO BET", "nan": "NO BET", "None": "NO BET"}) != "NO BET"
        if "Signal_Total" in sub.columns
        else pd.Series(False, index=sub.index)
    )
    has_spr = (
        sub["Signal_Handicap"].astype(str).str.strip().replace({"": "NO BET", "nan": "NO BET", "None": "NO BET"}) != "NO BET"
        if "Signal_Handicap" in sub.columns
        else pd.Series(False, index=sub.index)
    )
    return sub[has_tot | has_spr].reset_index(drop=True)


def _run_pipeline(end_year: int, do_sync: bool, custom_matchs: pd.DataFrame | None = None):
    stats = cached_collect_stats(int(end_year))
    st.session_state["stats"] = stats

    if custom_matchs is not None:
        full_df = custom_matchs.copy()
        out_src = st.session_state.get("out_source", "")
    else:
        raw_players = stats.get("Raw Player Stats")
        full_df, out_src = build_todays_matchs_df(raw_players_df=raw_players)
        st.session_state["out_source"] = out_src

        if full_df.empty and do_sync:
            try:
                existing = read_slate_from_sheet()
                if existing is not None and not existing.empty:
                    full_df = existing
            except Exception as exc:
                st.warning(f"Lecture de Matchs_Ce_Soir impossible : {exc}")

    full_df = _apply_saved_ps3838_lines(full_df)

    # Conserve B2B, OUT et lignes PS3838 (Total et Handicap) en interne pour le moteur de projection
    st.session_state["full_matchs_df"] = full_df
    projections = (
        project_slate(full_df, stats)
        if (full_df is not None and not full_df.empty)
        else pd.DataFrame()
    )
    st.session_state["projections"] = projections

    # Table épurée (sans colonnes D, E, F, G) pour l'app et Matchs_Ce_Soir
    clean_display_df = _drop_cols_defg_app(full_df)
    if not projections.empty and not clean_display_df.empty and len(projections) == len(clean_display_df):
        clean_display_df["Signal_Total"] = projections["Signal_Total"].values
        clean_display_df["Signal_Handicap"] = projections["Signal_Handicap"].values
    st.session_state["matchs_df"] = clean_display_df

    if do_sync:
        try:
            updated_matchs = push_all_to_google_sheets(
                projections,
                stats,
                matchs_df=clean_display_df,
                full_matchs_df=full_df,
            )
            if updated_matchs is not None and not updated_matchs.empty:
                st.session_state["matchs_df"] = _drop_cols_defg_app(updated_matchs)
            st.success(f"Stats, matchs du soir, absents ({out_src or 'manuel'}) et signaux synchronisés sur Google Sheets.")
        except Exception as exc:
            st.warning(f"Calcul terminé, mais la synchronisation Google Sheets a échoué : {exc}")
            st.exception(exc)
    else:
        st.success(f"Projections calculées (source absents : {out_src or 'manuel'}).")


if force_refresh_btn:
    _reset_pipeline_state()
    try:
        _run_pipeline(int(season_year), sync_sheets, custom_matchs=None)
    except Exception as exc:
        st.error(f"Erreur lors du rechargement forcé : {exc}")
        st.exception(exc)
elif run_btn:
    try:
        _run_pipeline(int(season_year), sync_sheets, custom_matchs=None)
    except Exception as exc:
        st.error(f"Erreur lors de l'exécution du moteur : {exc}")
        st.exception(exc)

if st.session_state.get("out_source"):
    with st.expander("📋 Logs absents détectés (RotoWire / Absents_Manuels)", expanded=True):
        st.code(st.session_state["out_source"], language="text")

if st.session_state.get("full_matchs_df") is not None and not st.session_state["full_matchs_df"].empty:
    with st.expander("🎯 Lignes PS3838 du soir — Total & Handicap (sans taper de signe moins)", expanded=True):
        st.caption(
            "Les lignes saisies restent en mémoire tant que la liste des matchs est identique. "
            "Entre `PS3838_Total`, choisis `Favori` (`Home` ou `Away`) et saisis `Handicap_Abs` (valeur positive)."
        )
        base_lines_df = _apply_saved_ps3838_lines(st.session_state["full_matchs_df"]).reset_index(drop=True)
        saved_map = dict(st.session_state.get("manual_ps3838_lines") or {})

        helper_rows = []
        for idx, r in base_lines_df.iterrows():
            m_key = str(r.get("Match", "")).strip()
            away_t = str(r.get("Away_Team", "")).strip()
            home_t = str(r.get("Home_Team", "")).strip()
            tot_val = pd.to_numeric(r.get("PS3838_Total"), errors="coerce")
            spr_val = pd.to_numeric(r.get("PS3838_Home_Spread"), errors="coerce")

            saved_item = saved_map.get(m_key, {})
            fav_default = saved_item.get("Favori", "Home")
            spread_abs_default = saved_item.get("Handicap_Abs", None)

            if spread_abs_default is None and not np.isnan(spr_val):
                spread_abs_default = abs(float(spr_val))
                fav_default = "Home" if float(spr_val) <= 0 else "Away"

            helper_rows.append({
                "Match": m_key,
                "Away": away_t,
                "Home": home_t,
                "PS3838_Total": None if np.isnan(tot_val) else float(tot_val),
                "Favori": fav_default,
                "Handicap_Abs": spread_abs_default,
            })

        helper_df = pd.DataFrame(helper_rows)
        edited_helper = st.data_editor(
            helper_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Favori": st.column_config.SelectboxColumn(
                    "Favori",
                    options=["Home", "Away"],
                    required=True,
                ),
                "Handicap_Abs": st.column_config.NumberColumn(
                    "Handicap (valeur positive)",
                    min_value=0.0,
                    max_value=40.0,
                    step=0.5,
                    format="%.1f",
                ),
                "PS3838_Total": st.column_config.NumberColumn(
                    "PS3838_Total",
                    min_value=150.0,
                    max_value=320.0,
                    step=0.5,
                    format="%.1f",
                ),
            },
            key="ps3838_helper_editor",
        )

        col_recalc, col_clear = st.columns([3, 1])
        with col_recalc:
            recalc_ps_clicked = st.button("⚡ Recalculer avec ces lignes PS3838", use_container_width=True)
        with col_clear:
            clear_ps_clicked = st.button("🧹 Effacer les lignes PS3838", use_container_width=True)

        if clear_ps_clicked:
            st.session_state["manual_ps3838_lines"] = {}
            cleaned_full = base_lines_df.copy()
            cleaned_full["PS3838_Total"] = np.nan
            cleaned_full["PS3838_Home_Spread"] = np.nan
            st.session_state["full_matchs_df"] = cleaned_full
            st.rerun()

        if recalc_ps_clicked:
            if st.session_state.get("stats") is not None:
                merged_df = base_lines_df.copy()
                for idx, r in edited_helper.iterrows():
                    m_key = str(r.get("Match", "")).strip()
                    tot_val = pd.to_numeric(r.get("PS3838_Total"), errors="coerce")
                    fav_side = str(r.get("Favori", "Home")).strip()
                    abs_sp = pd.to_numeric(r.get("Handicap_Abs"), errors="coerce")

                    if not np.isnan(abs_sp) and abs_sp > 0:
                        abs_pos = abs(float(abs_sp))
                        home_spread_signed = -abs_pos if fav_side == "Home" else abs_pos
                    elif not np.isnan(abs_sp) and abs_sp == 0:
                        home_spread_signed = 0.0
                    else:
                        home_spread_signed = np.nan

                    if m_key:
                        saved_map[m_key] = {
                            "PS3838_Total": None if np.isnan(tot_val) else float(tot_val),
                            "PS3838_Home_Spread": None if np.isnan(home_spread_signed) else float(home_spread_signed),
                            "Favori": fav_side,
                            "Handicap_Abs": None if np.isnan(abs_sp) else abs(float(abs_sp)),
                        }

                    if idx < len(merged_df):
                        merged_df.at[idx, "PS3838_Total"] = np.nan if np.isnan(tot_val) else float(tot_val)
                        merged_df.at[idx, "PS3838_Home_Spread"] = home_spread_signed

                st.session_state["manual_ps3838_lines"] = saved_map
                try:
                    _run_pipeline(int(season_year), sync_sheets, custom_matchs=merged_df)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erreur lors du recalcul avec lignes PS3838 : {exc}")
                    st.exception(exc)
            else:
                st.info("Lance d'abord les projections au moins une fois.")

st.subheader("1. Matchs_Ce_Soir & Paris à prendre")
if st.session_state["matchs_df"] is not None and not st.session_state["matchs_df"].empty:
    if st.session_state["out_source"]:
        st.caption(f"Source blessures/absents prise en compte dans le calcul : {st.session_state['out_source']}")
    edited_matchs = st.data_editor(
        st.session_state["matchs_df"],
        num_rows="dynamic",
        use_container_width=True,
        key="matchs_editor",
    )
    st.session_state["matchs_df"] = edited_matchs

    value_bets_df = _extract_value_bets_only(edited_matchs)
    if not value_bets_df.empty:
        st.markdown("**Value bets détectés à prendre :**")
        st.dataframe(value_bets_df, use_container_width=True)

    if st.button("Recalculer & mettre à jour Matchs_Ce_Soir"):
        if st.session_state["stats"] is not None:
            try:
                base_for_calc = (
                    st.session_state["full_matchs_df"]
                    if st.session_state["full_matchs_df"] is not None and len(st.session_state["full_matchs_df"]) == len(edited_matchs)
                    else edited_matchs
                )
                _run_pipeline(int(season_year), sync_sheets, custom_matchs=base_for_calc)
            except Exception as exc:
                st.error(f"Erreur lors du recalcul : {exc}")
                st.exception(exc)
        else:
            st.info("Cliquez d'abord sur 'Lancer / Actualiser les projections'.")
else:
    st.info("Cliquez sur 'Lancer / Actualiser les projections' pour importer automatiquement les matchs du jour et les joueurs OUT.")

st.subheader("2. Projections_Value (incluant projections PS3838)")
if st.session_state["projections"] is not None and not st.session_state["projections"].empty:
    st.dataframe(st.session_state["projections"], use_container_width=True)
else:
    st.info("Aucune projection affichée pour le moment.")

if st.session_state.get("stats") is not None:
    with st.expander("3. Aperçu des tables statistiques"):
        tab_names = list(st.session_state["stats"].keys())
        selected_tab = st.selectbox("Choisir un onglet de données", tab_names)
        st.dataframe(st.session_state["stats"][selected_tab], use_container_width=True)
