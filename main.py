import argparse
import os
import logging
import pandas as pd
from src.scraper import collect_all_stats
from src.model import project_slate
from src.sheets_sync import read_slate_from_sheet, push_all_to_google_sheets
from src.config import GOOGLE_SHEET_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="NBA Value Betting Engine -> Google Sheets")
    parser.add_argument("--season", type=int, default=2026, help="Année de fin de saison NBA (ex: 2026 pour 2025-26)")
    parser.add_argument("--hist-xlsx", type=str, default=None, help="Chemin vers l'ancien fichier Excel pour calibrer historique/std")
    parser.add_argument("--local-only", action="store_true", help="Exporte uniquement en local (Excel/CSV) sans Google Sheets")
    args = parser.parse_args()

    logger.info("=== 1. Collecte et normalisation des statistiques NBA ===")
    stats = collect_all_stats(season_end_year=args.season, historical_xlsx_path=args.hist_xlsx)

    logger.info("=== 2. Chargement de la carte des matchs & cotes PS3838 ===")
    slate_df = None
    if not args.local_only and GOOGLE_SHEET_ID:
        try:
            slate_df = read_slate_from_sheet()
            logger.info("Carte des matchs chargée depuis l'onglet Google Sheets 'Matchs_Ce_Soir'.")
        except Exception as exc:
            logger.warning(f"Lecture Google Sheets impossible ({exc}), bascule sur data/matchs_ce_soir.csv")

    if slate_df is None or slate_df.empty:
        csv_path = os.path.join("data", "matchs_ce_soir.csv")
        slate_df = pd.read_csv(csv_path)
        logger.info(f"Carte des matchs chargée depuis {csv_path}")

    logger.info("=== 3. Calcul des projections, probabilités et détection de Value ===")
    projections_df = project_slate(slate_df, stats)

    print("\n--- RÉSULTATS DES PROJECTIONS & VALUE BETS ---")
    if projections_df.empty:
        print("Aucun match valide trouvé dans la carte du soir.")
    else:
        display_cols = [
            "Match", "Proj_Away", "Proj_Home", "Proj_Total", "PS3838_Total",
            "Diff_Total", "Signal_Total", "EV_Total_%", "Mise_Total_EUR",
            "Proj_Handicap_Home", "PS3838_Handicap_Home", "Signal_Handicap", "EV_Handicap_%", "Mise_Handicap_EUR"
        ]
        print(projections_df[display_cols].to_string(index=False))

    # Sauvegarde locale systématique de secours
    os.makedirs("output", exist_ok=True)
    out_xlsx = os.path.join("output", "nba_projections_export.xlsx")
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        projections_df.to_excel(writer, sheet_name="Projections_Value", index=False)
        for sheet_name, df in stats.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    logger.info(f"Sauvegarde locale générée : {out_xlsx}")

    # Synchronisation Google Sheets
    if not args.local_only and GOOGLE_SHEET_ID:
        logger.info("=== 4. Synchronisation vers Google Sheets ===")
        push_all_to_google_sheets(projections_df, stats)
        logger.info("Synchronisation Google Sheets terminée avec succès !")
    elif not args.local_only:
        logger.info("GOOGLE_SHEET_ID non configuré dans .env : export Google Sheets ignoré (voir README.md).")


if __name__ == "__main__":
    main()
