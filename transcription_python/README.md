# Transcription Python de l'API UPB

Cette application Flask remplace les scripts PHP par des routes Python tout en
conservant les chemins `.php` attendus par l'interface et les intégrations
existantes. Les fichiers PHP d'origine restent dans le dossier parent.

## Organisation

- `app/config.py` : configuration par variables d'environnement.
- `app/db.py` : connexion PostgreSQL et cycle de vie par requête.
- `app/auth.py` : authentification Bearer des routes d'administration.
- `app/student_helpers.py` : matricules, dates, références et encodage.
- `app/routes/students.py` : vérification, inscription et modifications.
- `app/routes/admin.py` : gestion admin, demandes et import CSV.
- `app/routes/attendance.py` : rapports et enregistrement des présences.
- `static/` : interface HTML, CSS, JavaScript et logo.
- `migrations/` : migrations SQL fournies avec le projet.

## Installation locale (PowerShell)

Le schéma PostgreSQL principal doit déjà contenir les tables `etudiant`,
`filiere`, `niveau` et `candidat_concours`. Les migrations présentes ne créent
pas ce schéma de base. Vérifiez les migrations déjà appliquées puis exécutez,
si nécessaire, celles du dossier `migrations/` dans l'ordre numérique.

```powershell
cd .\transcription_python
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:UPB_DB_HOST = "localhost"
$env:UPB_DB_NAME = "upb_concours"
$env:UPB_DB_USER = "postgres"
$env:UPB_DB_PASS = "mot-de-passe-de-la-base"
$env:UPB_ADMIN_TOKEN = "remplacer-par-un-jeton-aleatoire-de-32-caracteres"
python run.py
```

Le service écoute sur `http://127.0.0.1:5000` par défaut. Ne réutilisez pas les
valeurs d'exemple en production. Configurez les secrets dans l'environnement
privé du serveur et utilisez HTTPS. L'application n'inclut aucun mot de passe
de base de données par défaut.

Variables disponibles :

| Variable | Défaut | Rôle |
|---|---|---|
| `UPB_DB_HOST` | `localhost` | Hôte PostgreSQL |
| `UPB_DB_NAME` | `upb_concours` | Base PostgreSQL |
| `UPB_DB_USER` | `postgres` | Utilisateur PostgreSQL |
| `UPB_DB_PASS` | aucun | Mot de passe PostgreSQL |
| `UPB_DB_PORT` | `5432` | Port PostgreSQL |
| `UPB_DB_DRIVER` | `pgsql` | Seul PostgreSQL est pris en charge |
| `UPB_ADMIN_TOKEN` | aucun | Jeton Bearer admin, 10 caractères minimum |
| `UPB_DEFAULT_PER_PAGE` | `20` | Pagination par défaut |
| `UPB_MAX_PER_PAGE` | `100` | Taille maximale d'une page |

## API

Les chemins d'origine sont conservés : `/verifier.php`,
`/demander_modification.php`, `/inscrire.php`, `/get_etudiants.php`,
`/update_etudiant.php`, `/update_telephone.php`,
`/admin_modifications.php`, `/admin_auth_check.php`,
`/import_etudiants.php` et `/pointage.php`. Les endpoints d'administration
requièrent `Authorization: Bearer <UPB_ADMIN_TOKEN>`. Les corps JSON, les
réponses et les codes HTTP suivent les mêmes contrats qu'avant.

## Tests

Depuis `transcription_python/`, exécutez :

```powershell
python -m unittest discover -s tests -v
```
