# AGENTS.md

Projet : traduction française de Higurashi Rei.

Règle principale :
Ne jamais modifier les fichiers du dossier Update sans préserver la structure technique du script.

Objectif :
Créer une VF cohérente avec les tomes français 1 à 8, en utilisant le repo de référence higurashi-fr-reference.

Interdictions :
- ne pas modifier les fonctions ;
- ne pas modifier les variables ;
- ne pas modifier les appels voix/sprites/musiques/backgrounds ;
- ne pas modifier les chemins ou noms de fichiers ;
- ne pas supprimer de lignes techniques ;
- ne pas traduire les identifiants ou paramètres.

Autorisé :
- traduire uniquement le texte joueur ;
- créer des outils dans tools/ ;
- créer un CSV de suivi dans translations/ ;
- proposer des modifications structurelles seulement si elles sont clairement signalées.

Workflow :
1. Extraire les lignes traduisibles dans un CSV.
2. Traduire par blocs.
3. Réinjecter seulement les traductions validées.
4. Vérifier l’intégrité du script.
5. Tester en jeu.

Ne pas faire de traduction massive non vérifiée.