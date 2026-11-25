import streamlit as st
import pandas as pd
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


@st.cache_data(ttl=1800, show_spinner="Collecte des statistiques NBA en cours...")
def cached_collect_stats(end_year: int):
    return collect_all_stats(season_end_year=end_year, historical_xlsx_path=None)


if "stats" not in st.session_state:
    st.session_state["stats"] = None
if "matchs_df" not in st.session_state:
    st.session_state["matchs_df"] = None
if "out_source" not in st.session_state:
    st.session_state["out_source"] = ""
if "projections" not in st.session_state:
    st.session_state["projections"] = None

if run_btn:
    try:
        stats = cached_collect_stats(int(season_year))
        st.session_state["stats"] = stats

        raw_players = stats.get("Raw Player Stats")
        todays_df, out_src = build_todays_matchs_df(raw_players_df=raw_players)
        st.session_state["out_source"] = out_src

        if todays_df.empty and sync_sheets:
            existing = read_slate_from_sheet()
            if existing is not None and not existing.empty:
                todays_df = existing

        st.session_state["matchs_df"] = todays_df
        projections = project_slate(todays_df, stats)
        st.session_state["projections"] = projections

        if sync_sheets:
            updated_matchs = push_all_to_google_sheets(projections, stats, matchs_df=todays_df)
            if updated_matchs is not None and not updated_matchs.empty:
                st.session_state["matchs_df"] = updated_matchs
            st.success(f"Stats, matchs du soir, absents ({out_src}) et signaux synchronisés sur Google Sheets.")
        else:
            st.success(f"Projections calculées (source absents : {out_src}).")
    except Exception as exc:
        st.error(f"Erreur lors de l'exécution du moteur : {exc}")
        st.exception(exc)

st.subheader("1. Matchs du soir, B2B, Absents (OUT) & Paris à prendre")
if st.session_state["matchs_df"] is not None and not st.session_state["matchs_df"].empty:
    if st.session_state["out_source"]:
        st.caption(f"Source blessures/absents : {st.session_state['out_source']}")
    edited_matchs = st.data_editor(
        st.session_state["matchs_df"],
        num_rows="dynamic",
        use_container_width=True,
        key="matchs_editor",
    )
    st.session_state["matchs_df"] = edited_matchs

    if st.button("Recalculer & mettre à jour Matchs_Ce_Soir"):
        if st.session_state["stats"] is not None:
            proj = project_slate(edited_matchs, st.session_state["stats"])
            st.session_state["projections"] = proj
            if sync_sheets:
                try:
                    updated_m = push_all_to_google_sheets(proj, st.session_state["stats"], matchs_df=edited_matchs)
                    if updated_m is not None and not updated_m.empty:
                        st.session_state["matchs_df"] = updated_m
                    st.success("Projections et signaux mis à jour dans Matchs_Ce_Soir.")
                except Exception as exc:
                    st.warning(f"Recalcul effectué, mais sync Google Sheets impossible : {exc}")
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
