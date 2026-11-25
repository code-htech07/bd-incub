-- Les noms, prénoms, allergies/affections, matricules, filières et niveaux sont toujours enregistrés en MAJUSCULES,
-- quelle que soit la façon dont ils sont saisis (portail, administration, import CSV, API, console SQL).
-- Les accents français sont convertis explicitement : le résultat ne dépend pas de la locale de la base.

CREATE OR REPLACE FUNCTION upb_majuscules(valeur TEXT) RETURNS TEXT AS $$
    SELECT UPPER(translate(valeur, 'àâäçéèêëîïôöùûüÿœæ', 'ÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ'))
$$ LANGUAGE SQL IMMUTABLE;

CREATE OR REPLACE FUNCTION etudiant_majuscules() RETURNS trigger AS $$
BEGIN
    NEW.matricule := upb_majuscules(NEW.matricule);
    NEW.nom := upb_majuscules(NEW.nom);
    NEW.prenoms := upb_majuscules(NEW.prenoms);
    NEW.allergies_affection := upb_majuscules(NEW.allergies_affection);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION libelle_majuscules() RETURNS trigger AS $$
BEGIN
    NEW.libelle := upb_majuscules(NEW.libelle);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Libellés qui ne diffèrent que par la casse : on rattache les étudiants au libellé en majuscules existant
UPDATE etudiant e SET filiere_id = t.id
    FROM filiere f, filiere t
    WHERE e.filiere_id = f.id AND f.libelle <> upb_majuscules(f.libelle)
      AND t.libelle = upb_majuscules(f.libelle) AND t.id <> f.id;
DELETE FROM filiere f USING filiere t
    WHERE f.libelle <> upb_majuscules(f.libelle) AND t.libelle = upb_majuscules(f.libelle) AND t.id <> f.id;
UPDATE etudiant e SET niveau_id = t.id
    FROM niveau f, niveau t
    WHERE e.niveau_id = f.id AND f.libelle <> upb_majuscules(f.libelle)
      AND t.libelle = upb_majuscules(f.libelle) AND t.id <> f.id;
DELETE FROM niveau f USING niveau t
    WHERE f.libelle <> upb_majuscules(f.libelle) AND t.libelle = upb_majuscules(f.libelle) AND t.id <> f.id;

DROP TRIGGER IF EXISTS trg_filiere_majuscules ON filiere;
CREATE TRIGGER trg_filiere_majuscules BEFORE INSERT OR UPDATE ON filiere
    FOR EACH ROW EXECUTE FUNCTION libelle_majuscules();
DROP TRIGGER IF EXISTS trg_niveau_majuscules ON niveau;
CREATE TRIGGER trg_niveau_majuscules BEFORE INSERT OR UPDATE ON niveau
    FOR EACH ROW EXECUTE FUNCTION libelle_majuscules();
DROP TRIGGER IF EXISTS trg_etudiant_majuscules ON etudiant;
CREATE TRIGGER trg_etudiant_majuscules BEFORE INSERT OR UPDATE ON etudiant
    FOR EACH ROW EXECUTE FUNCTION etudiant_majuscules();

-- Données existantes (les déclencheurs convertissent à l'écriture)
UPDATE filiere SET libelle = libelle WHERE libelle <> upb_majuscules(libelle);
UPDATE niveau SET libelle = libelle WHERE libelle <> upb_majuscules(libelle);
UPDATE etudiant SET nom = nom
    WHERE nom <> upb_majuscules(nom) OR prenoms <> upb_majuscules(prenoms)
       OR COALESCE(allergies_affection, '') <> COALESCE(upb_majuscules(allergies_affection), '')
       OR COALESCE(matricule, '') <> COALESCE(upb_majuscules(matricule), '');
