"""Anonymisation SQL via AST sqlglot : on ne touche QUE les valeurs littérales
(liées à une colonne), jamais la structure (mots-clés, tables, colonnes)."""
from __future__ import annotations
import re
import sqlglot
from sqlglot import exp
from sidecar.formats import FieldValue, _reinject_freetext


def _stmt_pairs(stmt) -> list[FieldValue]:
    pairs: list[FieldValue] = []
    if isinstance(stmt, exp.Insert):
        schema = stmt.this
        cols = [c.name for c in schema.expressions] if isinstance(schema, exp.Schema) else []
        values = stmt.expression
        if isinstance(values, exp.Values):
            for ri, tup in enumerate(values.expressions):
                for ci, e in enumerate(tup.expressions):
                    if isinstance(e, exp.Literal):
                        field = cols[ci] if ci < len(cols) else f"col{ci}"
                        pairs.append(FieldValue(field=field, value=e.this, path=f"ins{ri}.{ci}"))
        return pairs
    # UPDATE/DELETE/SELECT : on associe un contexte colonne aux littéraux quand possible
    # (=, !=, <, >, <=, >=, LIKE, IN), puis on RAMASSE TOUT littéral string restant
    # (filet de sécurité : aucune valeur ne doit fuir, même dans une forme non prévue).
    col_for: dict[int, str] = {}
    _cmp = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _cmp):
            left, right = node.this, node.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                col_for[id(right)] = left.name
        elif isinstance(node, exp.In):
            left = node.this
            if isinstance(left, exp.Column):
                for e in node.expressions:
                    if isinstance(e, exp.Literal):
                        col_for[id(e)] = left.name
    for i, lit in enumerate(stmt.find_all(exp.Literal)):
        field = col_for.get(id(lit))
        # littéral mappé à une colonne → toujours ; sinon filet sur les chaînes uniquement
        if field is not None:
            pairs.append(FieldValue(field=field, value=lit.this, path=f"lit{i}"))
        elif lit.is_string:
            pairs.append(FieldValue(field="valeur", value=lit.this, path=f"lit{i}"))
    return pairs


def extract_sql_pairs(sql: str) -> list[FieldValue]:
    out: list[FieldValue] = []
    try:
        statements = sqlglot.parse(sql)
    except Exception:
        return out
    for stmt in statements:
        if stmt is not None:
            out.extend(_stmt_pairs(stmt))
    return out


def reinject_sql(sql: str, replacement: dict) -> str:
    """Remplace les littéraux dont la valeur ∈ replacement par le token, SQL valide.
    Fallback remplacement chaîne si le parse/regen échoue (jamais de crash/fuite)."""
    try:
        statements = sqlglot.parse(sql)
        if not statements or all(s is None for s in statements):
            raise ValueError("unparseable")
        rendered = []
        for stmt in statements:
            if stmt is None:
                continue
            for lit in stmt.find_all(exp.Literal):
                if lit.this in replacement:
                    lit.set("this", replacement[lit.this])
                    lit.set("is_string", True)
            rendered.append(stmt.sql())
        return "; ".join(rendered)
    except Exception:
        return _reinject_freetext(sql, replacement)


_KW_RE = re.compile(r"(?is)\b(INSERT|UPDATE|DELETE|SELECT)\b")


def find_sql_statements(text: str) -> list[tuple[int, int, str]]:
    """Repère les fragments SQL dans un texte. Chaque candidat (mot-clé DML →
    ';' ou fin de ligne/texte) est VALIDÉ par sqlglot.parse. Non-chevauchants."""
    candidates: list[tuple[int, int, str]] = []
    for m in _KW_RE.finditer(text):
        start = m.start()
        semi = text.find(";", m.end())
        nl = text.find("\n", m.end())
        if semi != -1 and (nl == -1 or semi < nl):
            end = semi + 1
        elif nl != -1:
            end = nl
        else:
            end = len(text)
        frag = text[start:end]
        try:
            parsed = sqlglot.parse(frag)
        except Exception:
            continue
        if parsed and parsed[0] is not None:
            candidates.append((start, end, frag))
    candidates.sort()
    out: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, f in candidates:
        if s >= last_end:
            out.append((s, e, f))
            last_end = e
    return out


async def splice_embedded_sql(text: str, anonymize_sql):
    """anonymize_sql: async (sql)->(anon_sql, mapping{token:value}).
    Anonymise chaque span SQL et le recolle. Retourne (texte, mapping fusionné)."""
    spans = find_sql_statements(text)
    mapping: dict = {}
    for start, end, frag in sorted(spans, key=lambda x: x[0], reverse=True):
        anon_sql, span_map = await anonymize_sql(frag)
        text = text[:start] + anon_sql + text[end:]
        mapping.update(span_map)
    return text, mapping
