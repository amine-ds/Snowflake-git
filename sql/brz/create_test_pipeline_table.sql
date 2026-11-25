/*
    Objet        : Table TEST_PIPELINE
    Auteur       : CI/CD test
    Date         : 2026-11-25
    Description  : Table de test pour valider le pipeline DEV -> QUAL -> PRD.
*/
DECLARE
    --Déclaration de l'environnement, seule variable à modifier pour la livraison
    vEnv varchar := 'DEV';
    vZone varchar := 'BRZ';
    vDatabase varchar := :vEnv || '_' || :vZone;
    vTable varchar := 'SIDTRAFIC_BRZ.TEST_PIPELINE';
    vTableComplet varchar := :vDatabase || '.' || :vTable;
BEGIN
    USE DATABASE IDENTIFIER(:vDatabase);

    CREATE TABLE IF NOT EXISTS IDENTIFIER(:vTableComplet)
    (
        ID NUMBER COMMENT 'Identifiant technique',
        LB_LIBELLE VARCHAR COMMENT 'Libellé de test',
        DT_CREATION TIMESTAMP_NTZ COMMENT 'Date de création de la ligne'
    )
    COMMENT = 'Table créée pour valider le pipeline CI/CD (DEV -> QUAL -> PRD)';

    RETURN 'Table ' || :vTableComplet || ' créée avec succès';
END;
