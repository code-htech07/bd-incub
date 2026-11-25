-- Motif saisi par l'administrateur lorsqu'il refuse une demande (correction ou ajout de dossier).
-- Il est envoyé à l'étudiant par e-mail et conservé avec la demande. Facultatif, 500 caractères au plus (contrôlé par l'API).
ALTER TABLE etudiant_modification ADD COLUMN IF NOT EXISTS commentaire TEXT;
ALTER TABLE etudiant_creation_demande ADD COLUMN IF NOT EXISTS commentaire TEXT;
