# Prévisions CA HT — Pennylane + XGBoost

Application Streamlit autonome, en français, pour un dossier comptable. Créée à partir du fonctionnement souhaité de `YvamDarc/xgboost-app`, avec un moteur de validation et de sauvegarde réécrit. Aucun changement n'est nécessaire dans l'ancien dépôt.

## Mise en route sur Streamlit Cloud — sans installation locale

1. Décompresser le ZIP du code de cette application.
2. Sur GitHub, créer un **nouveau dépôt privé**, par exemple `pennylane-previsions`.
3. Ajouter les fichiers et dossiers décompressés à la racine du dépôt. `app.py`, `data.py`, `modeling.py`, `storage.py`, `ui_helpers.py` et `requirements.txt` doivent être au même niveau. Ajouter également `.streamlit/config.toml` (facultatif pour les couleurs), `README.md` et le script SQL. Ne pas charger le ZIP lui-même.
4. Ouvrir [Streamlit Community Cloud](https://share.streamlit.io/), créer une application, sélectionner ce dépôt, la branche `main`, et **Main file path : `app.py`**.
5. Dans les paramètres avancés, choisir **Python 3.12**, version utilisée pour les tests. Coller les secrets expliqués ci-dessous. Déployer.
6. Pour un premier essai sans secrets ni connexion : **Données & comptes → Démonstration → Charger la démonstration**.
7. **Variables** : le calendrier essentiel est déjà sélectionné. Aucun import complémentaire n'est nécessaire pour la démonstration.
8. **Entraînement** : garder les dates proposées ; mettre « Nombre de configurations à tester » à **1** pour un test rapide. Lancer. Le modèle s'arrête volontairement avant la fin du jeu de démonstration afin de comparer les premières prévisions au réel.
9. **Prévisions** : conserver les 30 jours proposés, cliquer sur « Créer et conserver cette prévision ».
10. **Écarts & questionnaire** : choisir une date puis « Analyser cette date », renseigner la fiche et enregistrer.
11. **Projet & sauvegardes → Télécharger le projet complet** pour conserver le travail si le stockage distant n'est pas configuré.

## Secrets Pennylane

Exemple à copier dans les paramètres Secrets de Streamlit, jamais dans GitHub :

```toml
APP_PASSWORD = "votre-mot-de-passe-long"
PENNYLANE_TOKEN = "votre-token-du-dossier"
# Pour un token cabinet uniquement :
# PENNYLANE_COMPANY_ID = "12345"
```

Le token doit permettre les lectures suivantes :

- `ledger_entries:readonly` ;
- `ledger_accounts:readonly` ;
- `journals:readonly`.

Un droit `:all` correspondant convient également, mais l'application n'effectue que des lectures sur Pennylane. Un token limité à la facturation ne suffit pas pour les mouvements comptables. L'identification du dossier se fait par le token ou `X-Company-Id` pour le token cabinet.

Le mot de passe protège l'accès dès qu'une connexion réelle est configurée. Les secrets ne sont ni exportés dans les projets ni inclus dans les modèles. Un fichier `secrets.example.toml` contient le modèle complet, sans vraie clé.

### Synchronisation

L'application récupère tous les comptes, journaux et mouvements de la période, avec pagination par curseur et relance limitée en cas de saturation API. Elle **remplace l'ancien instantané après succès intégral** : les corrections et suppressions sont donc prises en compte. Une erreur réseau conserve les anciennes données. Réduire la période si le volume est important ; ce choix privilégie la cohérence à une synchronisation incrémentale plus rapide.

Les mouvements sont ensuite filtrés localement. Cela permet de changer les comptes sans nouvel appel. La sélection est une union de préfixes, comptes exacts et plages, à laquelle les exclusions sont soustraites. Les plages utilisent une comparaison textuelle, bornes incluses : privilégier les préfixes ou des bornes et comptes de même longueur. Plusieurs comptes Pennylane portant le même numéro sont additionnés via leurs lignes distinctes.

**Règle demandée : chaque jour sans mouvement vaut zéro**, jusqu'à la date de comptabilité complète choisie par l'utilisateur. Il n'y a pas de détection automatique des retards de saisie, ni de fermeture forcée les dimanches ou jours fériés. Les montants négatifs ne sont pas arbitrairement ramenés à zéro.

Les contrôles fournissent le CA journalier, les comptes retenus et le CA par compte/mois. Les sommes comptables sont calculées en centimes.

Un import comptable CSV, TXT, XLSX ou XLSM avec correspondance des colonnes est disponible en alternative au connecteur. FEC : sélectionner `EcritureDate`, `CompteNum`, `CompteLib`, `JournalCode`, `Debit`, `Credit`. Les dates AAAAMMJJ et les décimales françaises sont prises en charge.

## Variables : fonctionnement commun à l'apprentissage et à la prévision

- Sélection rapide : essentiel, tout le calendrier, aucun calendrier, ou choix personnalisé.
- Jour de semaine, jour du mois, mois, année, jour de l'année, semaine ISO, trimestre, week-end.
- Férié, veille de férié et lendemain de férié : indicateurs 0/1 calculés automatiquement par `python-holidays`, France par défaut. Aucun effet commercial imposé.
- Pays disponibles : FR, BE, CH, LU ; subdivision facultative selon les codes acceptés par `python-holidays`.
- Retards : J−1, J−7, J−14, J−28, J−364. Moyennes sur les 7, 14 ou 28 derniers jours, **hors journée prédite**.
- Plusieurs fichiers externes, plusieurs variables par fichier. Nom de source + nom de colonne identifient chaque variable. Les variables peuvent être numériques ou catégorielles.
- En cas de plusieurs lignes par date : somme, moyenne, dernière valeur, minimum ou maximum pour les nombres ; dernière valeur pour les catégories.
- Les vacances scolaires, ponts particuliers, horaires, fermetures et promotions peuvent être importés sous forme de colonnes. Il n'y a pas de source automatique de vacances scolaires dans cette version.

Tous les imports historiques doivent couvrir la période d'entraînement retenue. Une absence de variable explicative n'est **pas** assimilée à une absence de CA. En prévision, si le modèle utilise des fichiers complémentaires, la même interface d'import apparaît pour chaque source ; **sans fichiers complets, le bouton de prévision est désactivé**. La trame Excel proposée reprend les dates nécessaires et, si elles existent déjà, les valeurs historiques correspondantes. Les catégories inconnues du modèle provoquent un message explicite : adapter la catégorie ou réentraîner.

La disponibilité à l'avance des variables est une hypothèse déclarée par l'utilisateur. Importer la météo réellement constatée dans un ancien test ne reproduit pas la qualité d'une prévision météo disponible à l'époque. L'application ne peut pas vérifier l'origine temporelle des fichiers fournis.

### Exemple des retards de CA

Le 31 octobre, on prévoit novembre. Pour le 1er novembre, J−7 = 25 octobre : CA historique connu. Pour le 15 novembre, J−7 = 8 novembre : CA encore inconnu au 31 octobre. L'application utilise **sa prévision du 8 novembre**, pas le réel du 8 novembre. Les tests font exactement la même chose.

Avec ces options, les dates intermédiaires depuis la fin d'apprentissage doivent également être calculées. Si l'on affiche seulement décembre avec un modèle arrêté au 31 octobre, novembre est prévu en interne ; les données externes doivent couvrir novembre et décembre. Les retards sont désactivés par défaut.

## Entraînement et validation

1. L'utilisateur choisit les bornes de l'apprentissage. Le jeu complet reste disponible pour comparer ensuite les dates exclues au réel.
2. La dernière période est réservée au test final. Si un délai est demandé, les jours du délai précédant le test sont également retirés de la sélection des paramètres.
3. Sur la partie de développement : apprentissage sur l'historique initial, prévision sur l'horizon, puis déplacement selon le pas demandé.
4. Fenêtre croissante : le début reste fixe. Fenêtre glissante : l'historique garde la taille initiale.
5. Le délai sépare le dernier jour d'apprentissage du premier jour évalué ; il est prévu en interne, sans réel futur injecté.
6. Tous les paramètres du formulaire sont réellement utilisés. Le moteur travaille sur un calendrier quotidien complet, donc un jour = une ligne.
7. Recherche aléatoire reproductible : 1 configuration = paramètres manuels ; davantage = comparaison de ces paramètres avec d'autres configurations. Budget maximal de 50, jusqu'à 100 simulations par configuration.
8. Choix de l'objectif : erreur quotidienne, total de période, ou cumul progressif ; MAE ou RMSE.
9. Le meilleur paramétrage est évalué une fois sur le test final avec un modèle entraîné avant ce test.
10. Le modèle de production est ensuite réentraîné sur **toute la période d'apprentissage demandée**, réserve comprise. Son identifiant et ses bornes sont affichés. Les résultats du test restent ceux du modèle antérieur, jamais recalculés avec le modèle de production.

Le modèle simple de comparaison utilise la moyenne des quatre derniers mêmes jours de semaine connus à l'origine. Pas de normalisation MinMax : les arbres n'en ont pas besoin, et la préparation est identique entre apprentissage et prévision. Les premières lignes nécessaires aux retards constituent la période de démarrage et ne sont pas utilisées comme cibles d'apprentissage.

### Trois lectures des erreurs

- **Journalier** : compare les CA jour par jour.
- **Total de période** : additionne réel et prévu dans chaque semaine, mois, trimestre ou bloc de N jours, puis calcule l'erreur sur ces totaux.
- **Cumul progressif** : compare les sommes progressives, remises à zéro à chaque nouvelle période et à chaque origine de simulation.

Un +1 000 € puis un −1 000 € donne une erreur absolue quotidienne moyenne de 1 000 €, une erreur sur le total de 0 €, et une erreur absolue moyenne sur la trajectoire cumulée de 500 €.

Les périodes partielles sont **incluses** dans le score de total et identifiées par « complète = False ». Pour optimiser de véritables totaux mensuels complets, choisir les dates, l'historique initial et l'horizon en conséquence et vérifier ce tableau. Les blocs de N jours commencent au premier jour évalué de chaque simulation. Un pas inférieur à l'horizon produit des prévisions multiples pour une même date à différentes origines : elles restent séparées, car ce sont des prévisions distinctes.

Biais signé = réel − prévu. SMAPE : les couples (0,0) contribuent 0. WAPE : somme des erreurs absolues divisée par somme des valeurs absolues réelles ; non défini si le dénominateur est nul.

Les bandes 80 % et 95 % reposent sur les quantiles d'erreurs absolues de la validation de sélection. Leur couverture est mesurée séparément sur le test final. Ce sont des bandes **indicatives**, possiblement optimistes après sélection de modèle, quotidiennes et non garanties ; elles ne constituent pas un intervalle calibré sur un cumul. Le modèle peut produire des montants négatifs ; pas de troncature automatique cachée.

## Prévisions et suivi

Chaque prévision conserve son modèle, sa date de création, son origine d'information, ses variables d'entrée et ses valeurs. Les prévisions ne sont jamais écrasées lors d'une synchronisation. Le réel est joint dynamiquement depuis la série actuelle, uniquement dans sa période de couverture ; les jours extérieurs restent inconnus. Si les comptes/journaux ont changé, la comparaison avec une ancienne prévision est désactivée pour éviter de mélanger les périmètres.

On ne peut sélectionner que des dates postérieures à la fin d'apprentissage du modèle. Pour tester des dates passées, entraîner une autre version qui s'arrête avant ces dates. La version de production étant réentraînée après le test final, ce dernier est consultable dans **Entraînement**, séparément de ses nouvelles prévisions.

Graphiques Plotly : quotidien, cumuls avec remise à zéro par période, bande indicative optionnelle. Export Excel : prévisions, réel, écarts, cumuls et hypothèses. L'écart en pourcentage est rapporté à la valeur absolue du prévu, et reste vide si ce dernier vaut zéro. Les plus gros écarts positifs et négatifs ont un seuil en euros configurable.

Le questionnaire est lié à une version de prévision et à une date ou plage de dates. Fiches modifiables, statut, causes supposées, action et commentaire. L'écart disponible au moment de l'enregistrement et le nombre de jours observés sont conservés. Les réponses ne sont pas réinjectées automatiquement comme variables d'entraînement.

## Sauvegarde persistante facultative avec Supabase

Sans configuration externe, tout fonctionne en mode portable : **exporter le ZIP du projet avant de fermer la session**, puis le réimporter à la prochaine utilisation. Le bouton d'export est disponible sans dépendre d'un entraînement réussi. Le disque local de Streamlit n'est pas utilisé comme base de sauvegarde.

Pour retrouver automatiquement le travail :

1. Créer un projet Supabase et ouvrir son SQL Editor.
2. Exécuter le contenu du fichier `supabase_setup.sql` fourni. La table n'est accessible ni au rôle anonyme ni au rôle authentifié public ; l'application utilise une clé serveur.
3. Ajouter dans les secrets Streamlit :

```toml
SUPABASE_URL = "https://votre-projet.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_votre-cle-serveur"
PROJECT_ID = "votre-identifiant-de-dossier"
```

4. Conserver `APP_PASSWORD`. La clé secrète reste côté serveur et ne doit jamais être fournie au navigateur ni publiée.
5. Au démarrage d'une nouvelle session, le projet est chargé automatiquement. L'enregistrement des données, des paramètres, d'un modèle, d'une prévision ou d'une fiche déclenche une sauvegarde.
6. Si une autre session a déjà modifié le projet, la version empêche l'écrasement : exporter d'abord votre travail, puis recharger le distant. Il n'y a pas de fusion automatique de sessions concurrentes.
7. Après restauration manuelle d'un ZIP, cliquer « Sauvegarder maintenant » pour l'enregistrer à distance.

Le stockage distant conserve le projet courant avec toutes ses versions de modèles et prévisions dans une archive compressée. Il remplace l'archive complète à chaque sauvegarde : adapté à un dossier et un usage ponctuel ; pour des millions de lignes ou de nombreux utilisateurs simultanés, prévoir une base normalisée et une synchronisation plus fine. Vérifier les quotas de votre offre de stockage. Exporter périodiquement une copie du projet reste utile.

## Contenu technique

- `app.py` : six écrans Streamlit.
- `data.py` : API Pennylane, import, comptes et agrégation.
- `modeling.py` : variables, XGBoost, récursivité, validation et métriques.
- `storage.py` : archive ZIP + JSON + modèles UBJ (aucun pickle), persistance Supabase.
- `ui_helpers.py` : import commun, graphiques et export Excel.
- `tests/` : tests du moteur, contrat API simulé, conflits et parcours Streamlit.

Les exports projet contiennent des données comptables et doivent rester privés. Ne pas les déposer dans le dépôt de code. Les exports Excel désactivent l'interprétation de chaînes comme formules.

### Exécution locale facultative

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Tests :

```bash
python -m pip install pytest
python -m pytest -q
```

## Vérifications et limites de livraison

Le parcours de démonstration et les calculs sont testés automatiquement. Le connecteur est construit sur la documentation officielle de l'API v2 et testé avec des réponses simulées reproduisant son schéma (pagination, comptes et journaux). **La connexion à votre dossier réel et la sauvegarde dans votre propre Supabase devront être vérifiées après saisie de vos secrets** : aucune clé réelle n'a été utilisée lors de la création de cette application.

Sources techniques consultées le 18/10/2026 :

- https://pennylane.readme.io/reference/getledgerentrylines
- https://pennylane.readme.io/reference/getledgeraccounts
- https://pennylane.readme.io/reference/getjournals
- https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest

La clé serveur moderne `sb_secret_…` est prise en charge via l’en-tête `apikey`. Les anciennes clés JWT `service_role` restent compatibles via le secret `SUPABASE_SERVICE_ROLE_KEY`.
