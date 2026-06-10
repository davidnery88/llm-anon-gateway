# Guide de Contribution

Merci de votre intérêt pour contribuer à LLM Anonymization Gateway !

## Licence

Ce projet est sous licence **PolyForm Noncommercial 1.0.0**. En contribuant, vous acceptez que vos contributions soient sous la même licence.

**Ce que cela signifie :**
- ✅ Vous pouvez contribuer pour usage personnel, éducatif, ou recherche
- ✅ Vous pouvez utiliser le projet en interne dans votre organisation (non-commercial)
- ❌ Vous ne pouvez pas contribuer dans le but de vendre le logiciel
- ❌ Vous ne pouvez pas intégrer vos contributions dans un produit commercial

## Comment Contribuer

### Signaler des Bugs

1. Vérifiez que le bug n'a pas déjà été signalé dans les Issues
2. Ouvrez une nouvelle Issue avec :
   - Description claire du problème
   - Étapes pour reproduire
   - Comportement attendu vs observé
   - Environnement (OS, Docker version, Python version)

### Proposer des Améliorations

1. Ouvrez d'abord une Issue pour discuter de votre proposition
2. Décrivez le problème que vous voulez résoudre
3. Proposez votre solution
4. Attendez les retours avant de coder

### Soumettre du Code

1. **Fork** le repository
2. **Clone** votre fork
3. Créez une **branche** pour votre fonctionnalité :
   ```bash
   git checkout -b feature/ma-fonctionnalite
   ```
4. **Codez** en suivant les conventions du projet
5. **Testez** votre code :
   ```bash
   # Tests sidecar
   cd sidecar && docker compose run --rm sidecar pytest sidecar/tests/ -v
   
   # Tests gateway
   cd gateway && docker compose run --rm gateway pytest gateway/tests/ -v
   ```
6. **Commit** avec des messages clairs :
   ```bash
   git commit -m "feat: ajoute support pour format XML"
   ```
7. **Push** vers votre fork
8. Ouvrez une **Pull Request**

## Conventions de Code

### Python

- **Style** : PEP 8
- **Formatage** : Utilisez `black` ou `ruff`
- **Type hints** : Obligatoires pour les fonctions publiques
- **Docstrings** : Format Google pour les fonctions publiques
- **Imports** : Triés avec `isort`

### Nommage

- **Fichiers** : `snake_case.py`
- **Classes** : `PascalCase`
- **Fonctions** : `snake_case`
- **Constantes** : `UPPER_SNAKE_CASE`

### Commits

Format conventionnel recommandé :
```
type(scope): description courte

[corps optionnel]

[footer optionnel]
```

Types :
- `feat` : nouvelle fonctionnalité
- `fix` : correction de bug
- `docs` : documentation
- `style` : formatage, point-virgules, etc.
- `refactor` : refactoring sans changement fonctionnel
- `test` : ajout ou modification de tests
- `chore` : maintenance, dépendances, etc.

## Structure du Projet

```
llm-anon-gateway/
├── gateway/              # Serveur central (métadonnées uniquement)
│   ├── main.py          # Point d'entrée FastAPI
│   ├── *_router.py      # Routes API
│   └── tests/           # Tests pytest
├── sidecar/             # Proxy local (NER + anonymisation)
│   ├── main.py          # Point d'entrée FastAPI
│   ├── proxy.py         # Proxy transparent
│   ├── anonymizer.py    # Orchestration NER
│   ├── ner.py           # Moteur NER (GLiNER + Presidio)
│   └── tests/           # Tests pytest
├── mcp_server/          # Serveur MCP pour Claude Code
├── frontend/            # UI statique (HTML/JS)
├── demo/                # Base de démo et scripts
├── docs/                # Documentation
└── scripts/             # Scripts utilitaires
```

## Tests

**Obligatoire** : Toute nouvelle fonctionnalité doit inclure des tests.

### Exécuter les tests

```bash
# Tests sidecar (NER, proxy, anonymisation)
cd sidecar
docker compose run --rm sidecar pytest sidecar/tests/ -v

# Tests gateway (API, auth, KB)
cd gateway
docker compose run --rm gateway pytest gateway/tests/ -v

# Tests spécifiques
docker compose run --rm sidecar pytest sidecar/tests/test_proxy_anonymizer.py -v
```

### Couverture

Visez au moins 80% de couverture pour le nouveau code.

## Documentation

- **README.md** : Vue d'ensemble et démarrage rapide
- **docs/SECURITY.md** : Modèle de sécurité
- **ROADMAP.md** : Feuille de route
- **Docstrings** : Documentation inline du code

## Questions ?

- **Issues** : Pour les bugs et discussions techniques
- **Email** : david@neri.contact pour questions sur la licence ou contributions commerciales

## Code de Conduite

- Soyez respectueux et constructif
- Acceptez les critiques et feedbacks
- Concentrez-vous sur ce qui est meilleur pour le projet
- Soyez patient avec les nouveaux contributeurs

---

Merci de contribuer à rendre LLM Anonymization Gateway meilleur !
