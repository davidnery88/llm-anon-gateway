# Conventions `docs/superpowers/`

- **`HANDOFF.md`** — état de reprise **roulant** : un seul fichier, **écrasé à chaque session**.
  Pas de date dans le nom : `git log --follow docs/superpowers/HANDOFF.md` sert d'archive datée.
- **`specs/`** et **`plans/`** — artefacts *write-once* liés à une feature, nommés
  `YYYY-MM-DD-<topic>.md` (specs : suffixe `-design`). La date = provenance + tri chrono ;
  on les retrouve par le `<topic>`. On **conserve** la date, pas d'écrasement.
