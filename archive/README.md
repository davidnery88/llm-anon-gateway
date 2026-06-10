# archive/

Trucs préservés pour référence mais hors path actif. Rien d'ici n'est
importé/référencé par le code en cours.

| Dossier | Contenu | Raison |
|---|---|---|
| `litellm/` | Vieux `config.yaml` du proto initial | Remplacé par le pipeline NER+sidecar. |
| `hooks_legacy/` | Hooks UserPromptSubmit + Stop qui anonymisaient via /anonymize | Remplacés par le proxy `sidecar/proxy.py` qui intercepte tout le trafic Claude Code ↔ Anthropic en un seul point. Le proxy est cohérent et couvre aussi les tool_result, ce que les hooks ne pouvaient pas. |

Si tu retrouves un import qui pointe ici, c'est un bug — soit le code
référençant doit être mis à jour, soit le fichier doit être restauré
volontairement.
