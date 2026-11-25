import os
import streamlit as st
import pandas as pd
from src.scraper import collect_all_stats
from src.model import project_slate
from src.sheets_sync import read_slate_from_sheet, push_all_to_google_sheets

st.set_page_config(
    page_title="NBA Value Betting Engine",
    page_icon="🏀",
    layout="wide",
)

st.title("🏀 NBA Value Betting Engine")
st.caption("Pilotage des projections NBA, malus absents/B2B et détection de Value face à PS3838")

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


def load_slate(use_sheets: bool) -> pd.DataFrame:
    if use_sheets:
        try:
            df = read_slate_from_sheet()
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            st.warning(f"Lecture Google Sheets indisponible ({exc}), repli sur data/matchs_ce_soir.csv.")
    csv_path = os.path.join("data", "matchs_ce_soir.csv")
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path)
    return pd.DataFrame(columns=[
        "Home_Team", "Away_Team", "Home_B2B", "Away_B2B",
        "Home_Out", "Away_Out", "PS3838_Total", "Odds_Over",
        "Odds_Under", "PS3838_Home_Spread", "Odds_Home_Spread", "Odds_Away_Spread"
    ])


if "stats" not in st.session_state:
    st.session_state["stats"] = None
if "slate_df" not in st.session_state:
    st.session_state["slate_df"] = load_slate(sync_sheets)

if run_btn or st.session_state["stats"] is None:
    try:
        stats = cached_collect_stats(int(season_year))
        st.session_state["stats"] = stats
        slate_current = st.session_state["slate_df"]
        projections = project_slate(slate_current, stats)
        st.session_state["projections"] = projections

        if sync_sheets:
            push_all_to_google_sheets(projections, stats)
            st.success("Projections calculées et synchronisées avec Google Sheets.")
    except Exception as exc:
        st.error(f"Erreur ({type(exc).__name__}) : {repr(exc)}")
        st.exception(exc)

st.subheader("1. Carte des matchs & cotes PS3838")
edited_slate = st.data_editor(
    st.session_state["slate_df"],
    num_rows="dynamic",
    use_container_width=True,
    key="slate_editor",
)
st.session_state["slate_df"] = edited_slate

if st.button("Recalculer sur les matchs modifiés", use_container_width=False):
    if st.session_state["stats"] is not None:
        proj = project_slate(edited_slate, st.session_state["stats"])
        st.session_state["projections"] = proj
        if sync_sheets:
            try:
                push_all_to_google_sheets(proj, st.session_state["stats"])
                st.success("Nouvelles projections poussées vers Google Sheets.")
            except Exception as exc:
                st.warning(f"Recalcul effectué localement, mais export Google Sheets impossible : {exc}")
    else:
        st.info("Cliquez d'abord sur 'Lancer / Actualiser les projections' pour charger les stats.")

st.subheader("2. Projections & Value Bets")
proj_df = st.session_state.get("projections")
if proj_df is not None and not proj_df.empty:
    st.dataframe(proj_df, use_container_width=True)
else:
    st.info("Aucune projection affichée pour le moment.")

if st.session_state.get("stats") is not None:
    with st.expander("3. Aperçu des tables statistiques collectées"):
        tab_names = list(st.session_state["stats"].keys())
        selected_tab = st.selectbox("Choisir un onglet de données", tab_names)
        st.dataframe(st.session_state["stats"][selected_tab], use_container_width=True)
