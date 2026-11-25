# NBA Value Betting Engine (Python + Google Sheets)

Pipeline automatisé d'extraction des statistiques NBA, d'ajustement contextuel (absents, Back-to-Back, forme 14 jours, écarts-types) et de détection de **Value Bets** (`Over/Under` et `Handicap`) face aux lignes **PS3838**, avec synchronisation directe vers **Google Sheets**.

## Architecture du projet

```text
nba_betting_engine/
├── main.py                  # Point d'entrée principal (collecte -> modèle -> Google Sheets)
├── requirements.txt         # Dépendances Python
├── .env.example             # Variables d'environnement à copier en .env
├── data/
│   └── matchs_ce_soir.csv   # Modèle de saisie locale (si Google Sheets non utilisé)
└── src/
    ├── config.py            # Paramètres du modèle, Kelly, mapping des 30 équipes NBA
    ├── scraper.py           # Scraping & construction des onglets stats
    ├── model.py             # Moteur de projection, malus absents/B2B, lois normales & EV
    └── sheets_sync.py       # Lecture/écriture automatique sur Google Sheets
```

## Onglets générés automatiquement

1. `Projections_Value` : Scores projetés, totaux, handicaps, écarts vs PS3838, probabilités, EV% et mises Kelly.
2. `Matchs_Ce_Soir` : Onglet interactif où saisir les matchs du soir, les absents (`Home_Out` / `Away_Out`), les `B2B` (`0`/`1`) et les lignes/cotes **PS3838**.
3. `Team Ratings Table` : Adjusted ORtg, DRtg et Net Rating des 30 franchises.
4. `Raw Player Stats` : Stats complètes des joueurs NBA.
5. `PTS last 2 weeks` : Moyenne de points des joueurs sur les 14 derniers jours.
6. `Points per game` & `opponent points per game` : Moyennes offensives/défensives (Saison, Last 3, Last 1, Home, Away).
7. `Points per game Seasons` : Comparaison saison actuelle vs saisons précédentes.
8. `Win % per game` : Pourcentages de victoires par contexte.
9. `Standard deviations` : Écarts-types Domicile / Extérieur calibrés par équipe.
10. *(Optionnel)* `Assists per game` & `Rebounds per game` en passant `INCLUDE_AST_RBD=true` dans `.env`.

## Installation rapide

```bash
pip install -r requirements.txt
cp .env.example .env
```

### Configuration Google Sheets (5 minutes)
1. Créez un projet sur **Google Cloud Console** et activez **Google Sheets API** + **Google Drive API**.
2. Créez un **Compte de service** (*Service Account*), générez une clé JSON et enregistrez-la sous `service_account.json` à la racine du projet.
3. Partagez votre Google Sheet avec l'adresse e-mail du compte de service (droits **Éditeur**).
4. Renseignez `GOOGLE_SHEET_ID` dans `.env`.

## Utilisation

```bash
# Exécution standard avec synchronisation Google Sheets
python main.py --season 2026

# En calibrant l'historique et les écarts-types à partir de votre ancien fichier Excel
python main.py --season 2026 --hist-xlsx "Simulation NBA games.xlsm.xlsx"

# Test en local uniquement (génère output/nba_projections_export.xlsx)
python main.py --local-only
```
