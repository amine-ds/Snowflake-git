/*
    Objet        : Table dynamique TEST_PIPELINE_DYN
    Auteur       : CI/CD test
    Date         : 2026-11-25
    Description  : Table dynamique de test, pour valider la réécriture du
                   warehouse (NP_COMPUTE_WH en non-prod -> COMPUTE_WH en prod).
*/
DECLARE
    --Déclaration de l'environnement, seule variable à modifier pour la livraison
    vEnv varchar := 'DEV';
    --Une seule base par environnement ; les zones sont des schémas à l'intérieur
    vSchema varchar := 'SLV';
    vTable varchar := 'TEST_PIPELINE_DYN';
    vTableComplet varchar := :vEnv || '.' || :vSchema || '.' || :vTable;
    vSource varchar := :vEnv || '.BRZ.TEST_PIPELINE';
BEGIN
    USE DATABASE IDENTIFIER(:vEnv);

    -- WAREHOUSE en dur : écrit ici en non-prod (NP_COMPUTE_WH), le
    -- déploiement retire automatiquement le préfixe NP_ en prod.
    CREATE OR REPLACE DYNAMIC TABLE IDENTIFIER(:vTableComplet)
    (
        ID NUMBER COMMENT 'Identifiant technique',
        LB_LIBELLE VARCHAR COMMENT 'Libellé de test',
        DT_CREATION TIMESTAMP_NTZ COMMENT 'Date de création de la ligne'
    )
    TARGET_LAG = DOWNSTREAM
    WAREHOUSE = NP_COMPUTE_WH
    REFRESH_MODE = INCREMENTAL
    INITIALIZE = ON_CREATE
    COMMENT = 'Table dynamique de test pour la pipeline CI/CD'
    AS
    SELECT
        ID,
        LB_LIBELLE,
        DT_CREATION
    FROM IDENTIFIER(:vSource);

    RETURN 'Table dynamique ' || :vTableComplet || ' créée avec succès';
END;
