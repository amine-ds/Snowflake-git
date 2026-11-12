#!/usr/bin/env python3
"""Lint des scripts SQL Snowflake avant déploiement.

Vérifie les bonnes pratiques attendues par l'équipe Manitowoc :
  - pas de SELECT * (hors COUNT(*))
  - utilisation de variables de session pour l'environnement / la base cible,
    pas de nom de base en dur (PRD_xxx / PPR_xxx)
  - présence d'un bloc DECLARE ... vEnv ... pour piloter l'environnement
  - présence d'un en-tête de commentaire (Auteur / Date / Description)
  - pas de mot de passe ou de clé en clair dans le script
  - fichier rangé sous sql/<brz|slv|gld>/... (ordonnancement des
    dépendances entre scripts lors du déploiement, voir README section 5)

Usage: python lint_sql.py fichier1.sql fichier2.sql ...
Exit code != 0 si au moins un fichier contient une violation bloquante.
"""
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

VALID_ZONES = {"brz", "slv", "gld"}


@dataclass
class Violation:
    file: str
    line: int
    rule: str
    message: str
    blocking: bool = True


def strip_comments(sql: str) -> str:
    """Retire les commentaires -- et /* */ pour éviter les faux positifs."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def check_select_star(raw: str, path: str) -> list[Violation]:
    violations = []
    for i, line in enumerate(raw.splitlines(), start=1):
        clean = strip_comments(line)
        if re.search(r"\bSELECT\s+\*", clean, re.IGNORECASE):
            if not re.search(r"COUNT\s*\(\s*\*\s*\)", clean, re.IGNORECASE):
                violations.append(
                    Violation(path, i, "no-select-star", "SELECT * interdit : lister explicitement les colonnes.")
                )
    return violations


def check_env_variable(sql_no_comments: str, path: str) -> list[Violation]:
    violations = []
    if not re.search(r"\bvEnv\b\s+varchar", sql_no_comments, re.IGNORECASE):
        violations.append(
            Violation(
                path, 1, "require-venv-variable",
                "Variable vEnv (DECLARE vEnv varchar := ...) absente : "
                "l'environnement cible doit être piloté par variable, pas codé en dur.",
            )
        )
    return violations


def check_hardcoded_db(sql_no_comments: str, path: str) -> list[Violation]:
    violations = []
    for i, line in enumerate(sql_no_comments.splitlines(), start=1):
        for m in re.finditer(r"\b(PRD|PPR)_[A-Z0-9_]+\b", line):
            violations.append(
                Violation(
                    path, i, "no-hardcoded-database",
                    f"Nom de base/schéma en dur détecté ('{m.group(0)}') : "
                    "utiliser les variables vDatabase/vEnv au lieu d'un littéral.",
                )
            )
    return violations


def check_header_comment(raw: str, path: str) -> list[Violation]:
    violations = []
    head = raw[:1500]
    has_header = bool(re.search(r"/\*.*?(Auteur|Date).*?\*/", head, re.IGNORECASE | re.DOTALL))
    if not has_header:
        violations.append(
            Violation(
                path, 1, "require-header-comment",
                "En-tête de commentaire manquant (bloc /* ... Auteur / Date / Description ... */).",
            )
        )
    return violations


def check_secrets(raw: str, path: str) -> list[Violation]:
    violations = []
    patterns = [
        (r"IDENTIFIED\s+BY\s*'[^']+'", "Mot de passe en clair détecté (IDENTIFIED BY)."),
        (r"-----BEGIN (RSA )?PRIVATE KEY-----", "Clé privée en clair détectée dans le script."),
    ]
    for pattern, message in patterns:
        for i, line in enumerate(raw.splitlines(), start=1):
            if re.search(pattern, line, re.IGNORECASE):
                violations.append(Violation(path, i, "no-secrets-in-sql", message))
    return violations


def check_zone_folder(path: str) -> list[Violation]:
    parts = Path(path).parts
    violations = []
    if len(parts) < 3 or parts[0].lower() != "sql" or parts[1].lower() not in VALID_ZONES:
        violations.append(
            Violation(
                path, 1, "require-zone-folder",
                "Le script doit être rangé sous sql/brz/, sql/slv/ ou sql/gld/ "
                "pour que le déploiement respecte l'ordre des dépendances entre zones.",
            )
        )
    return violations


def lint_file(path: str) -> list[Violation]:
    raw = Path(path).read_text(encoding="utf-8")
    clean = strip_comments(raw)
    violations: list[Violation] = []
    violations += check_select_star(raw, path)
    violations += check_env_variable(clean, path)
    violations += check_hardcoded_db(clean, path)
    violations += check_header_comment(raw, path)
    violations += check_secrets(raw, path)
    violations += check_zone_folder(path)
    return violations


def main(argv: list[str]) -> int:
    if not argv:
        print("Aucun fichier SQL à analyser.")
        return 0

    all_violations: list[Violation] = []
    for file_path in argv:
        if not file_path.strip():
            continue
        all_violations += lint_file(file_path)

    if not all_violations:
        print(f"✅ Lint OK sur {len(argv)} fichier(s).")
        return 0

    print(f"❌ {len(all_violations)} violation(s) détectée(s) :\n")
    for v in all_violations:
        print(f"  {v.file}:{v.line} [{v.rule}] {v.message}")

    blocking = [v for v in all_violations if v.blocking]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
