-- Le matricule n'est pas calculé par le système : il est fourni (saisi par l'étudiant ou présent dans le
-- fichier d'import). À l'import, seuls le nom et les prénoms sont obligatoires : un étudiant peut donc
-- exister sans matricule, qu'un administrateur renseignera ensuite (POST /api/update_etudiant).
-- La contrainte UNIQUE est conservée : PostgreSQL autorise plusieurs valeurs NULL.
ALTER TABLE etudiant ALTER COLUMN matricule DROP NOT NULL;
