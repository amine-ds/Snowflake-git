#!/usr/bin/env python3
"""Déploie des scripts SQL Snowflake (blocs anonymes DECLARE/BEGIN/END) sur
l'environnement cible, en forçant la variable vEnv du script, ainsi que le
préfixe d'environnement de tout warehouse référencé en dur (tables
dynamiques, tasks...), pour éviter toute erreur humaine (oubli de repasser
le script en PRD avant merge, warehouse resté sur le mauvais environnement,
etc.).

Chaque fichier .sql est envoyé tel quel en UNE seule instruction : les scripts
du client sont des blocs anonymes (DECLARE ... BEGIN ... END;) qui doivent
être exécutés d'un bloc, pas découpés sur les ';' internes.

Authentification : clé RSA (key-pair auth), pas de mot de passe stocké.

Variables d'environnement attendues :
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY,
  SNOWFLAKE_PRIVATE_KEY_PASSPHRASE (optionnel), SNOWFLAKE_ROLE,
  SNOWFLAKE_WAREHOUSE, TARGET_ENV (PPR ou PRD)

Ordre de déploiement (gestion des dépendances entre scripts) :
  1. Les fichiers sont d'abord regroupés par zone (dossier sql/<zone>/...)
     et déployés zone par zone dans l'ordre LAYER_ORDER ci-dessous
     (bronze avant silver avant gold), car une dépendance entre zones va
     presque toujours dans ce sens.
  2. Au sein d'une même zone, tri alphabétique par défaut.
  3. Si un script dépend d'un autre script modifié dans la MÊME PR (cas
     plus rare, ex. deux scripts de la même zone), le déclarer dans son
     propre en-tête avec un commentaire :
         --DEPLOY_AFTER: sql/slv/autre_script.sql
     (plusieurs chemins possibles, séparés par des virgules). Ce choix -
     une déclaration dans le fichier qui en a besoin, plutôt qu'un fichier
     manifeste partagé à la racine du repo - évite les conflits Git entre
     PR concurrentes qui auraient sinon dû modifier le même fichier.
     Une dépendance vers un script déjà déployé par une PR précédente
     n'a pas besoin d'être déclarée : elle n'apparaît pas dans la liste
     des fichiers modifiés de la PR courante, donc n'affecte pas le tri.

Usage: python deploy_snowflake.py fichier1.sql fichier2.sql ...
"""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import snowflake.connector

ALLOWED_ENVS = {"PPR", "PRD"}


def log(message: str) -> None:
    """Log horodaté : utile pour situer où un déploiement multi-fichiers a
    échoué quand les logs du job GitHub Actions défilent vite."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {message}")


def write_step_summary(markdown: str) -> None:
    """Ajoute `markdown` au résumé du job (onglet Summary du run GitHub
    Actions). No-op hors CI (GITHUB_STEP_SUMMARY non défini)."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write(markdown)


def describe_snowflake_error(exc: Exception) -> str:
    """Extrait les champs utiles d'une erreur snowflake-connector (code
    d'erreur, sqlstate, query ID) pour retrouver la cause exacte dans
    l'historique Snowflake (Query History), sans avoir à aller chercher
    dans les logs applicatifs."""
    parts = [f"{exc.__class__.__name__}: {exc}"]
    errno = getattr(exc, "errno", None)
    sqlstate = getattr(exc, "sqlstate", None)
    sfqid = getattr(exc, "sfqid", None)
    if errno is not None:
        parts.append(f"errno={errno}")
    if sqlstate:
        parts.append(f"sqlstate={sqlstate}")
    if sfqid:
        parts.append(f"query_id={sfqid} (cf. Snowflake > Query History)")
    return " | ".join(parts)

# Ordre de déploiement des zones, de la source vers les couches dérivées.
LAYER_ORDER = ["brz", "slv", "gld"]

DEPLOY_AFTER_PATTERN = re.compile(r"--\s*DEPLOY_AFTER\s*:\s*(.+)", re.IGNORECASE)


def zone_of(path: str) -> str | None:
    """Extrait le nom de zone depuis sql/<zone>/... (insensible à la casse)."""
    parts = Path(path).parts
    if len(parts) >= 2 and parts[0].lower() == "sql":
        return parts[1].lower()
    return None


def base_sort_key(path: str) -> tuple[int, str]:
    zone = zone_of(path)
    rank = LAYER_ORDER.index(zone) if zone in LAYER_ORDER else len(LAYER_ORDER)
    return (rank, path)


def read_deploy_after(file_path: str) -> list[str]:
    """Lit les dépendances --DEPLOY_AFTER: déclarées dans l'en-tête du fichier."""
    text = Path(file_path).read_text(encoding="utf-8")
    deps: list[str] = []
    for match in DEPLOY_AFTER_PATTERN.finditer(text):
        deps += [p.strip() for p in match.group(1).split(",") if p.strip()]
    return deps


def sort_deployment_order(files: list[str]) -> list[str]:
    """Tri topologique : respecte les dépendances --DEPLOY_AFTER: déclarées
    dans chaque fichier, puis départage par zone (brz < slv < gld) et ordre
    alphabétique. Une dépendance pointant vers un fichier absent de `files`
    est ignorée (le script dépendant est déjà déployé, hors de cette PR)."""
    files_set = set(files)
    deps: dict[str, set[str]] = {
        f: {d for d in read_deploy_after(f) if d in files_set and d != f}
        for f in files
    }

    ordered: list[str] = []
    remaining = set(files)
    while remaining:
        ready = sorted(
            (f for f in remaining if deps[f] <= set(ordered)),
            key=base_sort_key,
        )
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise ValueError(f"Dépendance DEPLOY_AFTER circulaire ou invalide entre : {cycle}")
        picked = ready[0]
        ordered.append(picked)
        remaining.discard(picked)

    return ordered


def load_private_key(pem: str, passphrase: str | None) -> bytes:
    key = serialization.load_pem_private_key(
        pem.encode("utf-8"),
        password=passphrase.encode("utf-8") if passphrase else None,
        backend=default_backend(),
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def force_target_env(sql: str, target_env: str) -> str:
    """Remplace la valeur de vEnv par l'environnement cible du job, quelle
    que soit la valeur laissée dans le fichier par le développeur."""
    pattern = re.compile(r"(vEnv\s+varchar\s*:=\s*)'[^']*'", re.IGNORECASE)
    new_sql, count = pattern.subn(rf"\1'{target_env}'", sql, count=1)
    if count == 0:
        raise ValueError("Variable vEnv introuvable dans le script : déploiement refusé.")
    return new_sql


# WAREHOUSE = <ENV>_<nom>, utilisé par ex. par les tables dynamiques
# (WAREHOUSE = ...) et les tasks (WAREHOUSE = ...). Ces clauses attendent un
# identifiant littéral : on ne peut pas y mettre une variable de session
# (:vWarehouse), contrairement à vDatabase. On réécrit donc directement le
# préfixe d'environnement, comme pour vEnv, quel que soit celui laissé dans
# le fichier par le développeur.
WAREHOUSE_PATTERN = re.compile(
    r"(\bWAREHOUSE\s*=\s*)(" + "|".join(ALLOWED_ENVS) + r")(_[A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def force_target_warehouse_env(sql: str, target_env: str) -> str:
    """Réécrit le préfixe d'environnement de chaque warehouse référencé
    dans une clause WAREHOUSE = ..., en préservant le reste du nom."""
    return WAREHOUSE_PATTERN.sub(rf"\1{target_env}\3", sql)


def connect() -> snowflake.connector.SnowflakeConnection:
    private_key_der = load_private_key(
        os.environ["SNOWFLAKE_PRIVATE_KEY"],
        os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
    )
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=private_key_der,
        role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    )


def main(argv: list[str]) -> int:
    target_env = os.environ.get("TARGET_ENV", "")
    if target_env not in ALLOWED_ENVS:
        print(f"::error::TARGET_ENV invalide ou absent : '{target_env}' (attendu PPR ou PRD).")
        return 1

    files = [f for f in argv if f.strip()]
    if not files:
        log("Aucun fichier SQL à déployer.")
        return 0

    log(f"Déploiement demandé sur {target_env} pour {len(files)} fichier(s) : {', '.join(files)}")

    try:
        files = sort_deployment_order(files)
    except ValueError as exc:
        log(f"❌ Ordonnancement impossible : {exc}")
        print(f"::error title=Ordonnancement des dépendances::{exc}")
        write_step_summary(f"### Déploiement {target_env}\n\n❌ Ordonnancement impossible : {exc}\n")
        return 1

    log("Ordre de déploiement retenu :")
    for f in files:
        log(f"  - {f}")

    results: list[tuple[str, bool, str]] = []  # (fichier, succès, détail)
    conn = connect()
    log(f"Connecté à Snowflake (account={os.environ['SNOWFLAKE_ACCOUNT']}, role={os.environ['SNOWFLAKE_ROLE']}, warehouse={os.environ['SNOWFLAKE_WAREHOUSE']})")
    try:
        cur = conn.cursor()
        for file_path in files:
            start = time.monotonic()
            log(f"--- Déploiement de {file_path} sur {target_env} ---")
            try:
                with open(file_path, encoding="utf-8") as fh:
                    sql = fh.read()

                sql = force_target_env(sql, target_env)
                sql = force_target_warehouse_env(sql, target_env)

                cur.execute(sql)
                rows = list(cur)
                for row in rows:
                    log(f"  résultat : {row}")

                elapsed = time.monotonic() - start
                log(f"✅ {file_path} déployé en {elapsed:.1f}s")
                results.append((file_path, True, rows[0][0] if rows else "OK"))
            except Exception as exc:  # noqa: BLE001
                elapsed = time.monotonic() - start
                cause = describe_snowflake_error(exc)
                log(f"❌ Échec sur {file_path} après {elapsed:.1f}s : {cause}")
                print(f"::error file={file_path},title=Échec du déploiement {target_env}::{cause}")
                results.append((file_path, False, cause))
                raise
    except Exception as exc:  # noqa: BLE001
        log(f"❌ Déploiement {target_env} interrompu : {exc.__class__.__name__}: {exc}")
        write_step_summary(_summary(target_env, results, interrupted=True))
        return 1
    finally:
        conn.close()

    log(f"✅ {len(files)} script(s) déployé(s) sur {target_env}.")
    write_step_summary(_summary(target_env, results, interrupted=False))
    return 0


def _summary(target_env: str, results: list[tuple[str, bool, str]], interrupted: bool) -> str:
    rows = "\n".join(
        f"| `{f}` | {'✅' if ok else '❌'} | {detail} |" for f, ok, detail in results
    )
    status = "❌ Interrompu (voir la cause ci-dessous)" if interrupted else "✅ Terminé"
    return (
        f"### Déploiement {target_env}\n\n"
        f"{status}\n\n"
        "| Fichier | Statut | Détail |\n"
        "|---|---|---|\n"
        f"{rows}\n"
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
