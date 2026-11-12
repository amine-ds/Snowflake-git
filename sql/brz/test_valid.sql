/*
    Objet        : Script de test du lint (étape 1)
    Auteur       : CI/CD test
    Date         : 2026-11-12
    Description  : Script conforme, ne doit déclencher aucune violation.
*/
DECLARE
    vEnv varchar := 'PRD';
    vZone varchar := 'BRZ';
    vDatabase varchar := :vEnv || '_' || :vZone;
    vTable varchar := 'TEST_SCHEMA.TEST_TABLE';
    vTableComplet varchar := :vDatabase || '.' || :vTable;
BEGIN
    USE DATABASE IDENTIFIER(:vDatabase);
    RETURN 'Test lint OK pour ' || :vTableComplet;
END;
