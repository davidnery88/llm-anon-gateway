# Spec — Anonymisation du SQL brut (INSERT/UPDATE/DELETE/SELECT)

Date : 2026-06-08
Statut : validé (design approuvé section par section)

## Contexte & objectif

Quand du **SQL** transite dans un prompt (DML collé par un utilisateur via Claude Code),
masquer **uniquement les valeurs** (PII) sans abîmer la structure de la requête, et de façon
**réversible**. Aujourd'hui le SQL est traité comme du texte libre (GLiNER) : ça attrape une
partie de la PII mais peut tagger des mots-clés/colonnes, rater les identifiants à format, et
casser la syntaxe. Item issu du README « V2 → SQL brut INSERT/UPDATE (sqlglot) ».

Exemple :
```
INSERT INTO clients (nom, email) VALUES ('David Dupont', 'david@x.ch');
 →  INSERT INTO clients (nom, email) VALUES ('[PERSONNE_1]', '[EMAIL_1]');
```

## Décisions de design (actées)

1. **Instructions couvertes** : INSERT (VALUES), UPDATE (SET + WHERE), DELETE (WHERE), SELECT (WHERE).
2. **Détection** : **les deux modes** — (a) le texte EST du SQL (autonome), (b) SQL **embarqué** dans de la prose.
3. **Parser** : `sqlglot` (pur-Python, multi-dialectes) → nouvelle dépendance sidecar.
4. **Réutilisation** : chaque valeur est rattachée à une **colonne** → on réutilise la classification
   structurée existante (KB → Presidio → GLiNER avec hint → qwen). GLiNER reste utilisé, mais sur la
   **valeur avec contexte colonne**, pas sur la syntaxe.

## Architecture

Nouveau module `sidecar/sql_anon.py` (responsabilité unique : parsing/anonymisation SQL via AST) :
- `extract_sql_pairs(sql) -> list[FieldValue]` — AST → pour chaque littéral de valeur lié à une
  colonne (VALUES/SET/WHERE) : `FieldValue(field=colonne, value=littéral, path=…)`.
- `reinject_sql(sql, replacement) -> str` — re-parse, remplace les nœuds littéraux dont la valeur ∈
  `replacement` par le token, régénère un SQL valide. Fallback `_reinject_freetext` si la régénération échoue.
- `find_sql_statements(text) -> list[(start, end, sql)]` — repère les spans SQL dans un texte
  (regex candidate sur INSERT/UPDATE/DELETE/SELECT … jusqu'à `;`/fin), **validés par `sqlglot.parse`**.

Intégration :
- `sidecar/formats.py` : `detect_format` ajoute **"sql"** (commence par mot-clé DML ET parse OK) ;
  `extract_pairs`/`reinject` délèguent `"sql"` à `sql_anon`.
- `sidecar/anonymizer.py` :
  - **SQL autonome** → `_anonymize_structured` **inchangé** (utilise déjà `extract_pairs`/`reinject`).
  - **SQL embarqué** → pré-passe dans `_anonymize_freetext` : `find_sql_statements`, anonymiser chaque
    span (de la fin vers le début pour garder les offsets), recoller, **puis** NER sur le texte recollé
    (la prose ; le SQL est déjà tokenisé donc non re-taggé). Mappings fusionnés.
  - Petit refactor ciblé : extraire un helper `_classify_field(field, values, …)` de
    `_anonymize_structured`, partagé par les deux modes.

## Flux

**Autonome :** `detect_format → "sql"` → `extract_pairs(sql)` (colonne,valeur) → classification par
colonne → `replacement{valeur→token}` → `reinject_sql` (SQL valide).

**Embarqué :** dans freetext, repérer les spans SQL → anonymiser chaque span via sql_anon → recoller →
NER sur le reste.

Extraction par statement :
- `INSERT INTO t (c1,c2) VALUES (v1,v2),(v3,v4)` → (c1,v1)(c2,v2)(c1,v3)(c2,v4)
- `UPDATE t SET c1=v1 WHERE c2=v2` → (c1,v1)(c2,v2)
- `DELETE FROM t WHERE c=v` / `SELECT … WHERE c=v` → (c,v)
- littéraux chaînes + nombres ; NULL/booléens/fonctions ignorés ; nombres « montant » → `keep` → non touchés.

## Fail-safe (« rather fail than leak »)

- `detect` → "sql" **uniquement si parse OK** ; sinon freetext (GLiNER filet).
- Span embarqué qui ne parse pas → ignoré (laissé tel quel), couvert par le NER freetext.
- `reinject_sql` échoue → fallback remplacement chaîne.
- **Au pire = comportement actuel** (GLiNER sur texte brut) ; jamais de crash/fuite ; fail-safe global (503) inchangé.

## Edge cases

- `INSERT … VALUES` sans liste de colonnes → pas de hint → GLiNER sur la valeur (sans contexte).
- Valeurs identiques répétées → même token (cohérent).
- Multi-statements séparés par `;` → tous traités.
- Dialecte : parse générique sqlglot ; dialecte exotique qui casse → fail-safe freetext.

## Tests

- **Unitaires `sql_anon`** (cœur, pas de DB/modèle) : extraction des (colonne, valeur) pour
  INSERT/UPDATE/DELETE/SELECT ; `reinject_sql` masque seulement les valeurs et reste valide ;
  `find_sql_statements` repère le SQL dans de la prose ; SQL malformé → géré sans crash.
- **Bout-en-bout** (classeur qwen + NER mockés, convention des tests sidecar existants) :
  SQL autonome → valeurs tokenisées + structure intacte ; SQL embarqué → span masqué + prose NER'ée.
- **Fail-safe** : SQL malformé en entrée → retombe sur freetext, pas d'exception.

## Dépendances

`sqlglot` (ajouter à `sidecar/requirements.txt`).

## Hors scope (YAGNI)

- DDL (CREATE/ALTER) — pas de PII de données.
- Anonymisation des **noms de colonnes/tables** (jamais : c'est de la structure, pas de la PII).
- Détection de SQL multi-langues exotiques au-delà de ce que sqlglot parse en générique.
