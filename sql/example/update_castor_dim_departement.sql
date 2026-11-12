/**********************************************************************************
REVISIONS
    Date         : 2026-11-09
    Auteur       : Salah LACHEHEB
    Modification : Suite au ticket ORUS RITM0108272
    Changer la borne inférieure de la 'A89' de 539.05 à 539.043
**********************************************************************************/
--Déclaration des variables
DECLARE
    --Déclaration de l'environnement, seule variable à modifier pour la livraison
    vEnv varchar := 'PRD';
    --Déclaration de la table cible
    vZone     varchar := 'SLV';
    vDatabase varchar := :vEnv || '_' || :vZone;
    vTable        varchar := 'SIDTRAFIC_SLV.CASTOR_DIM_DEPARTEMENT';
    vTableComplet varchar := :vDatabase || '.' || :vTable;
--Script de création de la table
BEGIN
    --Utilisation de la database où créer la table
    USE DATABASE identifier(:vDatabase);
    -- Correction du PR de début de l'autoroute A89
    UPDATE IDENTIFIER(:vTableComplet)
        set CD_PR_DEBUT  = 539.043
    WHERE CD_AUTOROUTE = 'A89'
    AND   CD_PR_DEBUT  = 539.05
    ;
    --Message de retour confirmant la création
    RETURN 'Table ' || :vTableComplet || ' mise à jour avec succès';
END;
