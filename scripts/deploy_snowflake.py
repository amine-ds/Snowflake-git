#!/usr/bin/env python3
"""Déploie des scripts SQL Snowflake (blocs anonymes DECLARE/BEGIN/END) sur
l'environnement cible, en forçant la variable vEnv du script pour éviter
toute erreur humaine (oubli de repasser le script en PRD avant merge, etc.).

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
  3. Si un fichier deploy_order.txt est présent à la racine du repo (un
     chemin de script par ligne, relatif à la racine), il prime sur les
     deux règles précédentes pour les fichiers qu'il liste : à utiliser
     pour les cas particuliers où deux scripts de la même zone dépendent
     l'un de l'autre dans un ordre non alphabétique.

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

DEPLOY_ORDER_FILE = "deploy_order.txt"


def zone_of(path: str) -> str | None:
    """Extrait le nom de zone depuis sql/<zone>/... (insensible à la casse)."""
    parts = Path(path).parts
    if len(parts) >= 2 and parts[0].lower() == "sql":
        return parts[1].lower()
    return None


def sort_deployment_order(files: list[str]) -> list[str]:
    """Trie les fichiers à déployer : zone (brz < slv < gld) puis alpha,
    sauf override explicite via deploy_order.txt."""
    override = Path(DEPLOY_ORDER_FILE)
    if override.exists():
        ordered = [
            line.strip() for line in override.read_text(encoding="utf-8").splitlines()
            if line.strip() and line.strip() in files
        ]
        remaining = sorted(f for f in files if f not in ordered)
        if ordered:
            print(f"Ordre de déploiement défini par {DEPLOY_ORDER_FILE} pour {len(ordered)} fichier(s).")
        return ordered + remaining

    def sort_key(path: str) -> tuple[int, str]:
        zone = zone_of(path)
        try:
            rank = LAYER_ORDER.index(zone) if zone else len(LAYER_ORDER)
        except ValueError:
            rank = len(LAYER_ORDER)
        return (rank, path)

    return sorted(files, key=sort_key)


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
