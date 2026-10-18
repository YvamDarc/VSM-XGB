# Vérification de livraison

Tests exécutés le 18 octobre 2026 sous Python 3.12 : **12 tests réussis**.

- Comptes, signes débit/crédit, centimes et jours sans mouvement.
- Fusion des doublons externes et refus des variables manquantes.
- Génération future des fériés et veilles/lendemains.
- Scores journaliers, sur totaux et sur cumuls effectivement distincts.
- Prise en compte des paramètres temporels et séparation du test final.
- Absence d’utilisation du CA réel futur avec les retards récursifs.
- Blocage des prévisions sans variables externes nécessaires.
- Export/restauration du projet avec prévisions de modèle identiques.
- Schéma API Pennylane, curseurs et filtres simulés.
- Refus d’écrasement du projet en cas de conflit de version distant.
- Parcours Streamlit complet : démonstration, variables, entraînement, prévision, questionnaire, export.
- Protection de l’application par mot de passe.

Les versions de dépendances utilisées sont figées dans requirements.txt.

Aucune connexion à un vrai dossier Pennylane ni à un vrai projet Supabase n’a été effectuée : aucune clé utilisateur n’était disponible. Les réponses API et conflits sont simulés dans les tests. Le projet n’a pas été déployé sur Streamlit Cloud pendant cette livraison.
