# CI/CD Snowflake – Manitowoc

Pipeline de déploiement continu des scripts SQL Snowflake, déclenché à la
création/mise à jour d'une Pull Request, avec double validation manuelle
(preprod puis prod) par un admin Snowflake.

## Vue d'ensemble

```
PR ouverte / mise à jour (sql/**/*.sql)
        │
        ▼
   [lint]  ──── automatique, bloque la suite si KO
        │
        ▼
   [deploy-preprod]  ──── ⏸ attend l'approbation d'un reviewer de
        │                   l'Environment GitHub "preprod"
        ▼
   [deploy-prod]     ──── ⏸ attend l'approbation d'un reviewer de
                            l'Environment GitHub "prod"
```

- Le **lint** tourne sans intervention humaine dès l'ouverture de la PR.
- Le job **deploy-preprod** ne démarre qu'après validation manuelle d'un
  membre du groupe "Snowflake admin" (via les GitHub Environments).
- Le job **deploy-prod** ne démarre qu'après le succès du déploiement
  preprod **et** une seconde validation manuelle.
- Chaque environnement utilise son propre compte de service Snowflake,
  dont les droits sont limités à sa base cible (voir plus bas).

## 1. Arborescence attendue dans le repo

```
sql/                         scripts SQL à déployer (un fichier = un bloc
                             anonyme DECLARE/BEGIN/END)
  brz/                       scripts touchant la zone bronze
  slv/                       scripts touchant la zone silver
  gld/                       scripts touchant la zone gold
.github/workflows/snowflake-cicd.yml
scripts/lint_sql.py
scripts/deploy_snowflake.py
scripts/requirements.txt
```

Copier ce dossier `snowflake-cicd/` à la racine du repo GitHub (fusionner
son contenu avec l'existant).

## 2. Comptes de service Snowflake

Créer deux users Snowflake dédiés à la CI, authentifiés par **clé RSA**
(pas de mot de passe statique, recommandation Snowflake) :

```sql
-- Compte PREPROD : accès uniquement aux bases PPR_*
CREATE USER SVC_CICD_PREPROD
  RSA_PUBLIC_KEY = '<clé publique PEM>'
  DEFAULT_ROLE = ROLE_CICD_PREPROD
  DEFAULT_WAREHOUSE = <NOM_WAREHOUSE_PREPROD>
  MUST_CHANGE_PASSWORD = FALSE;

CREATE ROLE ROLE_CICD_PREPROD;
GRANT USAGE ON WAREHOUSE <NOM_WAREHOUSE_PREPROD> TO ROLE ROLE_CICD_PREPROD;
GRANT ALL ON DATABASE PPR_<ZONE> TO ROLE ROLE_CICD_PREPROD;  -- une ligne par zone (brz/slv/gld) à autoriser
GRANT ROLE ROLE_CICD_PREPROD TO USER SVC_CICD_PREPROD;

-- Compte PROD : accès uniquement aux bases PRD_*
CREATE USER SVC_CICD_PROD
  RSA_PUBLIC_KEY = '<clé publique PEM>'
  DEFAULT_ROLE = ROLE_CICD_PROD
  DEFAULT_WAREHOUSE = <NOM_WAREHOUSE_PROD>
  MUST_CHANGE_PASSWORD = FALSE;

CREATE ROLE ROLE_CICD_PROD;
GRANT USAGE ON WAREHOUSE <NOM_WAREHOUSE_PROD> TO ROLE ROLE_CICD_PROD;
GRANT ALL ON DATABASE PRD_<ZONE> TO ROLE ROLE_CICD_PROD;  -- une ligne par zone (brz/slv/gld) à autoriser
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

> Important : l'important n'est pas seulement "deux comptes" mais que
> chaque rôle n'ait **aucun droit** sur les objets de l'autre environnement
> (vérifier qu'aucun grant transverse n'existe, p. ex. sur un rôle public).

## 3. Configuration GitHub

### 3.1 Environments (la clé de la double validation)

`Settings > Environments` → créer deux environnements : `preprod` et `prod`.

Pour chacun :
- **Required reviewers** : ajouter le ou les comptes GitHub des admins
  Snowflake habilités à valider ce palier.
- (Recommandé) **Deployment branches** : restreindre à la branche `main`
  ou aux PR ciblant `main`.
- **Environment secrets** (différents par environnement) :
  - `SNOWFLAKE_ACCOUNT`
  - `SNOWFLAKE_USER` (`SVC_CICD_PREPROD` ou `SVC_CICD_PROD`)
  - `SNOWFLAKE_PRIVATE_KEY` (contenu complet de `rsa_key.p8`)
  - `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` (si vous avez chiffré la clé)
  - `SNOWFLAKE_ROLE` (`ROLE_CICD_PREPROD` ou `ROLE_CICD_PROD`)
  - `SNOWFLAKE_WAREHOUSE`

C'est cette configuration d'Environment qui fait apparaître, sur la PR, le
bouton "Review deployments" que l'admin doit cliquer pour laisser passer
le job.

### 3.2 Branch protection

`Settings > Branches` → règle sur `main` :
- Require a pull request before merging
- Require status checks to pass (`Lint SQL`) avant de pouvoir merger
- (Optionnel) Require approval d'au moins un reviewer de code, en plus de
  la validation "Environment" qui, elle, porte sur le déploiement.

## 4. Fonctionnement du lint (`scripts/lint_sql.py`)

Bloque la PR si un fichier `.sql` modifié contient :
- un `SELECT *` (hors `COUNT(*)`)
- pas de variable `vEnv` déclarée (l'environnement doit être piloté par
  variable, jamais codé en dur dans les `FROM`/`JOIN`)
- un nom de base/schéma en dur du type `PRD_...` ou `PPR_...` ailleurs que
  dans la déclaration de variable
- un warehouse (`WAREHOUSE = ...`) dont le nom n'est pas préfixé par un
  code d'environnement valide (`PRD_...` / `PPR_...`) — voir section 5
- pas d'en-tête de commentaire (Auteur / Date / Description)
- un mot de passe ou une clé privée écrits en clair dans le script

Ces règles sont volontairement simples (regex) pour démarrer vite ; elles
sont faciles à étendre dans ce même fichier au fur et à mesure des besoins
de l'équipe (ex. imposer `CREATE OR REPLACE`, interdire `DROP` sans
confirmation, etc.).

## 5. Fonctionnement du déploiement (`scripts/deploy_snowflake.py`)

Point important par rapport à vos scripts actuels : ils contiennent une
ligne `vEnv varchar := 'PRD'` que le développeur modifie à la main avant
la livraison. Pour éviter une erreur humaine (oubli de repasser la valeur
à `PRD` avant le merge, ou déploiement en PROD d'un script resté en PPR),
**le script de déploiement réécrit automatiquement cette valeur** en
fonction du job qui l'exécute (`PPR` pour le job preprod, `PRD` pour le
job prod), quelle que soit la valeur présente dans le fichier. Le
développeur n'a donc plus besoin d'y toucher.

**Même principe pour les warehouses.** Les tables dynamiques et les tasks
référencent leur warehouse en dur (`WAREHOUSE = PRD_TRF_TRAFIC_WH` par
exemple) : contrairement à `vDatabase`, cette clause n'accepte pas une
variable de session en DDL, donc le nom reste toujours écrit en clair dans
le script. Le déploiement repère chaque préfixe d'environnement
(`PRD_`/`PPR_`) dans une clause `WAREHOUSE = ...` et le réécrit pour
correspondre au job en cours, sur le même principe que `vEnv` — qu'il y en
ait un ou plusieurs dans le fichier (table dynamique, task...). Le lint
impose en contrepartie que tout warehouse référencé porte bien ce préfixe,
sans quoi la réécriture ne saurait pas quoi faire.

Chaque fichier `.sql` modifié par la PR est envoyé à Snowflake en une
seule requête (`cur.execute`), car vos scripts sont des blocs anonymes
`DECLARE ... BEGIN ... END;` qui doivent être exécutés d'un bloc.

### Gestion des dépendances entre scripts

Quand une PR modifie plusieurs fichiers `.sql` qui dépendent les uns des
autres (un script crée/alimente une table que lit un autre script), l'ordre
d'exécution compte. Le déploiement applique deux règles, dans cet ordre :

1. **Organiser `sql/` par zone** : `sql/brz/...`, `sql/slv/...`,
   `sql/gld/...`. Le script de déploiement déploie toujours les zones dans
   cet ordre fixe (bronze → silver → gold), quel que soit l'ordre renvoyé
   par `git diff`, car une dépendance entre zones va presque toujours de
   la source vers les couches dérivées.
2. **Au sein d'une même zone** (ou pour tout cas particulier), déclarer la
   dépendance **dans l'en-tête du script qui en a besoin** :
   ```sql
   --DEPLOY_AFTER: sql/slv/autre_script.sql
   ```
   (plusieurs chemins possibles, séparés par des virgules). Le script de
   déploiement fait alors un tri topologique : le(s) fichier(s) listé(s)
   sont déployés avant celui qui les déclare, et une dépendance circulaire
   fait échouer le job avec une erreur explicite. Le lint vérifie que le
   chemin référencé existe bien dans le repo.

   **Pourquoi pas un fichier manifeste partagé (`deploy_order.txt`) à la
   racine du repo ?** Parce que deux PR ouvertes en parallèle qui auraient
   chacune besoin d'y ajouter une ligne entreraient en conflit Git l'une
   avec l'autre au moment du merge — voire, pire, une PR basée sur un
   `main` plus ancien pourrait écraser silencieusement l'entrée ajoutée par
   une autre PR déjà mergée. En mettant la déclaration dans l'en-tête du
   fichier lui-même, chaque PR ne touche que les fichiers qu'elle modifie
   réellement : aucun fichier partagé, donc aucun risque de conflit.

Une dépendance vers un objet déployé lors d'une PR **précédente** (donc
déjà en base) n'a pas besoin d'être déclarée : ce fichier n'apparaît pas
dans la liste des fichiers modifiés de la PR courante, donc n'entre pas
dans le tri.

## 6. Ce qui reste à décider avec l'équipe

- **Rollback** : ces scripts sont `CREATE OR REPLACE` / `UPDATE`, donc pas
  de rollback automatique. À minima, consigner le lien du run GitHub
  Actions dans le ticket associé.
- **Merge** : la pipeline déploie **avant** le merge (sur la PR). Décider
  si le merge dans `main` se fait automatiquement après succès du job
  `deploy-prod`, ou reste manuel.
