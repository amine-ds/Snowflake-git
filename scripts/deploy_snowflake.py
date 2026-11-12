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

sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import snowflake.connector

ALLOWED_ENVS = {"PPR", "PRD"}

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
        print(f"TARGET_ENV invalide ou absent : '{target_env}' (attendu PPR ou PRD).")
        return 1

    files = [f for f in argv if f.strip()]
    if not files:
        print("Aucun fichier SQL à déployer.")
        return 0

    files = sort_deployment_order(files)
    print("Ordre de déploiement retenu :")
    for f in files:
        print(f"  - {f}")

    conn = connect()
    try:
        cur = conn.cursor()
        for file_path in files:
            with open(file_path, encoding="utf-8") as fh:
                sql = fh.read()

            sql = force_target_env(sql, target_env)
            sql = force_target_warehouse_env(sql, target_env)

            print(f"--- Déploiement de {file_path} sur {target_env} ---")
            cur.execute(sql)
            for row in cur:
                print(row)
        print(f"✅ {len(files)} script(s) déployé(s) sur {target_env}.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Échec du déploiement : {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
