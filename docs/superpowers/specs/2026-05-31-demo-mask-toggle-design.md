# Démo live Claude Code + interrupteur « masque »

**Date** : 2026-05-31
**Objectif** : remplacer la démo curl (`run_demo.sh`) par une session **Claude Code en vrai**, branchée sur le proxy sidecar, avec un **bouton web** qui bascule en live l'affichage des réponses entre vrais noms et jetons — pour montrer visuellement l'effet de masquage.

## Contexte

Le dry-run du 2026-05-31 a prouvé que le vrai Claude Code marche pour la phrase libre et le CSV (deanon proxy nickel). Seule la scène MCP `query_db` a déraillé : Claude a vu l'outil `deanonymize_text`, a voulu l'appeler, s'est bloqué sur une permission et a halluciné des noms. Le proxy désanonymise déjà tout seul ; ces outils MCP sont redondants et pièges en démo.

## Les 3 scènes de la démo (session Claude Code interactive)

1. **Phrase libre** — confirmer un truc à un client (nom + email + IBAN). Deanon auto. Prouvé fiable.
2. **CSV collé** — analyse churn d'un extrait clients. Deanon auto. Prouvé fiable.
3. **MCP `query_db`** — Claude interroge la vraie DB SQLite, raisonne sur des jetons, le proxy désanonymise sa réponse. Money shot.

Le fail-safe (couper le sidecar) n'est pas dans le script de démo.

## Le beat « masque »

Geste : **clique le bouton → re-pose la question → le public voit la bascule**. Le bouton agit sur la **prochaine** réponse, pas sur le texte déjà affiché.

- Masque **LEVÉ** (défaut, = comportement normal) : le terminal Claude Code affiche `Dubois, Müller, Lehmann`.
- Masque **MIS** : le terminal affiche `[PERSONNE_1], [PERSONNE_2]…` — exactement ce qui est parti chez Anthropic.

Punchline : « Anthropic ne voit QUE ça. Les vrais noms n'existent que sur mon poste. »

## Changements

### 1. Sidecar — interrupteur sur l'étape deanon

- Drapeau en mémoire `app.state.demo_mask_on` (défaut `False` = masque levé = deanon active).
- `POST /demo/mask {"on": true|false}` → écrit le drapeau. `GET /demo/mask` → lit l'état (pour le bouton).
- Dans `proxy.py` : si `demo_mask_on` est `True`, on **n'instancie pas** le `StreamDeanonymizer` (`deanon = None`) → les jetons passent bruts jusqu'au terminal.
- **Par défaut masque levé → comportement de prod strictement inchangé.** Gadget de démo, documenté comme tel.

### 2. Frontend — page bouton

- Petite page `frontend/demo_mask.html`, fond clair (cohérent avec les autres UI), gros bouton « 🎭 Masque : LEVÉ / MIS » qui appelle `POST /demo/mask` et reflète l'état via `GET /demo/mask`.
- CORS sidecar déjà ouvert pour l'origine frontend.

### 3. MCP — virer les outils pièges

- `mcp_server/server.py` : supprimer les outils `deanonymize_text` et `anonymize_sql_results`.
- `README.md` : retirer la mention de `anonymize_sql_results` (ligne ~78) devenue obsolète.
- Restent exposés : `query_db`, `set_anonymization`, `status`.

## Validation

- Relancer le MCP : `deanonymize_text` absent de la liste d'outils.
- 3 scènes en `claude --print` (ou interactif) : réponses désanonymisées, aucun arrêt permission, aucune hallucination.
- Bouton : masque MIS → réponse en jetons ; masque LEVÉ → vrais noms.
- Répéter les 3 scènes jusqu'à stabilité avant go/no-go.

## Hors scope

- Pas de toggle sur l'anonymisation outbound (ce qui PART vers le cloud) — `set_anonymization` côté MCP existe déjà si besoin.
- Pas de packaging / déploiement multi-poste.
