# snowflake-git — scripts SQL Snowflake

Ce dépôt contient le code SQL à déployer sur Snowflake, les 3 branches
longues `DEV`/`QUAL`/`PRD`, et les Environments GitHub `qual`/`prod` avec
leurs secrets. La logique de lint et de déploiement (scripts Python,
étapes du workflow) vit dans un dépôt séparé et mutualisé :
**[amine-ds/snowflake-admin](https://github.com/amine-ds/snowflake-admin)**,
invoquée ici via un reusable workflow — voir son README pour le détail de
ce mécanisme. Les secrets Snowflake, eux, restent dans **ce** dépôt (section
5 ci-dessous), puisque c'est lui qui possède les données qu'il déploie.

## 1. Modèle de branches

```
PRD ──(create branch TICKET-123)──> TICKET-123
                                        │
                                        ▼  merge, aucune pipeline
                                       DEV
                                        │
                                        ▼  PR vers QUAL : lint + déploiement
                                        │  automatique sur QUA
                                       QUAL
                                        │
                                        ▼  PR vers PRD : lint + déploiement
                                            sur PRD, validation manuelle requise
                                       PRD
```

- Une branche de ticket est créée **depuis `PRD`**, généralement nommée
  d'après le numéro de ticket.
- Merge dans `DEV` : **aucune pipeline ne se déclenche** (vous avez déjà
  testé manuellement sur votre environnement DEV avant ce merge).
- PR vers `QUAL` : lint + déploiement automatique sur `QUA`, sans
  validation manuelle.
- PR vers `PRD` : lint + déploiement sur `PRD`, avec validation manuelle
  obligatoire d'un admin Snowflake.

Comme auparavant, le déploiement a lieu **avant** le merge (sur la PR
elle-même) : attendez que la PR passe au vert avant de la merger.

**Après un déploiement réussi**, le workflow committe automatiquement sur
la branche de la PR la valeur réellement déployée (`vEnv`, warehouse) dans
le(s) fichier(s) concerné(s) — sinon le fichier garderait pour toujours ce
que vous avez écrit (ex. `vEnv='DEV'`), même après un déploiement prod
réussi. Un nouveau `git pull` sur votre branche de ticket après un run vert
vous montrera donc l'état réellement déployé.

## 2. Arborescence attendue

```
sql/
  brz/   scripts touchant la zone bronze
  slv/   scripts touchant la zone silver
  gld/   scripts touchant la zone gold
.github/workflows/snowflake-cicd.yml   workflow appelant (invoque snowflake-admin)
```

Chaque fichier `.sql` est un bloc anonyme Snowflake Scripting
(`DECLARE ... BEGIN ... END;`), déployé d'un bloc.

## 3. Conventions attendues par le lint

- Pas de `SELECT *` (hors `COUNT(*)`).
- Une variable `vEnv` déclarée (`DECLARE vEnv varchar := 'DEV'` par
  exemple) : elle sera automatiquement réécrite (`DEV`/`QUA`/`PRD`) par le
  déploiement selon la branche cible — inutile de la modifier à la main
  avant une PR.
- **Une seule base par environnement** (`DEV`, `QUA`, `PRD`) ; les zones
  (`BRZ`/`SLV`/`GLD`) sont des **schémas** à l'intérieur de cette base, pas
  des bases séparées. Référencer la base via `vEnv` (ex.
  `IDENTIFIER(:vEnv)`), jamais en dur.
- Pas de nom de base en dur (`DEV`/`QUA`/`PRD` utilisé tel quel ailleurs que
  dans la déclaration de la variable `vEnv`).
- Un warehouse référencé en dur (`WAREHOUSE = ...`, utilisé par les tables
  dynamiques et les tasks — cette clause n'accepte pas de variable de
  session) suit une convention différente : un warehouse non-prod est
  préfixé `NP_` (ex. `NP_COMPUTE_WH`, partagé par DEV et QUA), le même
  warehouse en prod n'a **pas** de préfixe (`COMPUTE_WH`). Le déploiement
  ajoute ou retire ce préfixe `NP_` automatiquement selon la cible — aucune
  contrainte du lint sur le nom lui-même, préfixé ou non dans le fichier.
- Un en-tête de commentaire (bloc `/* ... Auteur / Date / Description ... */`).
- Pas de mot de passe ni de clé privée en clair dans le script.

## 4. Dépendances entre scripts d'une même PR

Si un script dépend d'un autre modifié dans la **même PR** (cas plus rare,
en général entre scripts de la même zone), le déclarer dans son propre
en-tête :
```sql
--DEPLOY_AFTER: sql/slv/autre_script.sql
```
(plusieurs chemins possibles, séparés par des virgules). Le déploiement
respecte alors cet ordre avant celui des zones. Une dépendance vers un
script déjà déployé par une PR précédente n'a pas besoin d'être déclarée.

Pour le détail du fonctionnement du lint et du déploiement eux-mêmes (tri,
réécriture de `vEnv`/warehouse...), voir le README de
[snowflake-admin](https://github.com/amine-ds/snowflake-admin).

## 5. Mise en place (comptes de service, Environments, secrets)

### 5.1 Comptes de service Snowflake

Deux comptes de service suffisent (aucun déploiement automatique ne va sur
DEV). Une seule base par environnement (`QUA`, `PRD`) ; les zones
(`BRZ`/`SLV`/`GLD`) sont des schémas à l'intérieur :

```sql
-- Compte QUAL : accès uniquement à la base QUA
CREATE USER SVC_CICD_QUAL
  RSA_PUBLIC_KEY = '<clé publique PEM>'
  DEFAULT_ROLE = ROLE_CICD_QUAL
  DEFAULT_WAREHOUSE = <NOM_WAREHOUSE_QUAL>
  MUST_CHANGE_PASSWORD = FALSE;

CREATE ROLE ROLE_CICD_QUAL;
GRANT USAGE ON WAREHOUSE <NOM_WAREHOUSE_QUAL> TO ROLE ROLE_CICD_QUAL;
GRANT ALL ON DATABASE QUA TO ROLE ROLE_CICD_QUAL;
GRANT ALL ON ALL SCHEMAS IN DATABASE QUA TO ROLE ROLE_CICD_QUAL;
GRANT ALL ON FUTURE SCHEMAS IN DATABASE QUA TO ROLE ROLE_CICD_QUAL;
GRANT ALL ON ALL TABLES IN DATABASE QUA TO ROLE ROLE_CICD_QUAL;
GRANT ALL ON FUTURE TABLES IN DATABASE QUA TO ROLE ROLE_CICD_QUAL;
GRANT ROLE ROLE_CICD_QUAL TO USER SVC_CICD_QUAL;

-- Compte PROD : accès uniquement à la base PRD
CREATE USER SVC_CICD_PROD
  RSA_PUBLIC_KEY = '<clé publique PEM>'
  DEFAULT_ROLE = ROLE_CICD_PROD
  DEFAULT_WAREHOUSE = <NOM_WAREHOUSE_PROD>
  MUST_CHANGE_PASSWORD = FALSE;

CREATE ROLE ROLE_CICD_PROD;
GRANT USAGE ON WAREHOUSE <NOM_WAREHOUSE_PROD> TO ROLE ROLE_CICD_PROD;
GRANT ALL ON DATABASE PRD TO ROLE ROLE_CICD_PROD;
GRANT ALL ON ALL SCHEMAS IN DATABASE PRD TO ROLE ROLE_CICD_PROD;
GRANT ALL ON FUTURE SCHEMAS IN DATABASE PRD TO ROLE ROLE_CICD_PROD;
GRANT ALL ON ALL TABLES IN DATABASE PRD TO ROLE ROLE_CICD_PROD;
GRANT ALL ON FUTURE TABLES IN DATABASE PRD TO ROLE ROLE_CICD_PROD;
GRANT ROLE ROLE_CICD_PROD TO USER SVC_CICD_PROD;
```

Génération de la paire de clés (à faire une fois, en local, jamais commitée) :

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```
`rsa_key.pub` (sans les en-têtes `-----BEGIN/END-----`) va dans
`RSA_PUBLIC_KEY` côté Snowflake ; `rsa_key.p8` va dans le secret GitHub
`SNOWFLAKE_PRIVATE_KEY`.

> Important : chaque rôle ne doit avoir **aucun droit** sur les objets de
> l'autre environnement (vérifier qu'aucun grant transverse n'existe, p. ex.
> sur un rôle public).

### 5.2 GitHub Environments — DANS CE DÉPÔT (snowflake-git)

`Settings > Environments` (sur `amine-ds/Snowflake-git`) → créer deux
environnements :

**`qual`**
- Pas de "Required reviewers" (déploiement automatique, sans validation).
- Environment secrets : `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` (`SVC_CICD_QUAL`),
  `SNOWFLAKE_PRIVATE_KEY`, `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` (si clé
  chiffrée), `SNOWFLAKE_ROLE` (`ROLE_CICD_QUAL`), `SNOWFLAKE_WAREHOUSE`.

**`prod`**
- **Required reviewers** : ajouter le ou les comptes GitHub des admins
  Snowflake habilités à valider le déploiement prod.
- Ne PAS utiliser "Deployment branches and tags" restreint à une branche :
  pour un job déclenché par une PR, la branche du run est
  `refs/pull/<n>/merge`, jamais `PRD` littéralement — cette restriction
  rejetterait tous les déploiements (piège déjà rencontré avec l'ancien
  modèle à une seule branche `main`). Laisser "No restriction".
- Environment secrets : mêmes noms que ci-dessus, avec les valeurs du
  compte `SVC_CICD_PROD` / `ROLE_CICD_PROD`.

Ces noms d'Environment (`qual`/`prod`) doivent correspondre exactement à
ceux utilisés dans `.github/workflows/snowflake-cicd.yml`
(`environment_name` calculé selon la branche cible).
