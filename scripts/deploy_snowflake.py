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

Usage: python deploy_snowflake.py fichier1.sql fichier2.sql ...
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import snowflake.connector

ALLOWED_ENVS = {"PPR", "PRD"}


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
