# snowflake-git — scripts SQL Snowflake

Ce dépôt contient uniquement le code SQL à déployer sur Snowflake. La
logique de lint et de déploiement (scripts, workflow GitHub Actions,
secrets, validations) vit dans un dépôt séparé :
**[amine-ds/snowflake-admin](https://github.com/amine-ds/snowflake-admin)**
— voir son README pour l'architecture complète et la mise en place des
Environments/secrets.

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
- Pas de nom de base/schéma en dur (`DEV_...`/`QUA_...`/`PRD_...`) ailleurs
  que dans la déclaration de variable : utiliser `vDatabase`/`vEnv`.
- Un warehouse référencé en dur (`WAREHOUSE = ...`, utilisé par les tables
  dynamiques et les tasks — cette clause n'accepte pas de variable de
  session) doit être préfixé par un code d'environnement reconnu
  (`DEV_.../QUA_.../PRD_...`) : il sera lui aussi réécrit automatiquement
  au déploiement.
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

Pour le détail du fonctionnement du lint et du déploiement (secrets,
Environments GitHub, comptes de service Snowflake), voir le README de
[snowflake-admin](https://github.com/amine-ds/snowflake-admin).
