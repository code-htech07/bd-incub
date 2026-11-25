# Journal de restructuration

Objectif : projet 100 % FastAPI (backend) + HTML/CSS/JS (frontend), sans aucune
trace de PHP, avec PostgreSQL hébergé sur Neon. Rien n'est inventé : le code
Python est la transcription du PHP d'origine (via `transcription_python/`).

Chaque étape ci-dessous est ajoutée au moment où elle est réalisée.

## Étape 1 — Séparation frontend / backend et migrations SQL

- Création des dossiers `frontend/`, `backend/` et `docs/`.
- Déplacés vers `frontend/` (contenu inchangé à cette étape) :
  `index.html`, `admin.html`, `scanner.html`, `genie.css`, `genie-fx.js`, `logo-upb.png`.
- Déplacés vers `backend/migrations/` : `01_pointage.sql`, `02_etudiant_contact.sql`.
- Restauré depuis l'historique git (commit `7310468`) puis placé dans
  `backend/migrations/` : `03_inscription_migrations.sql`. Il avait été supprimé
  (commit `da5fb19`) alors que le code en dépend (table `etudiant_modification`,
  colonnes `date_naissance`, `allergies_affection`).

## Étape 2 — Backend FastAPI (`backend/`)

Transcription de la logique PHP (et de `transcription_python/`, base Flask) vers
FastAPI. Aucune règle métier nouvelle.

| PHP d'origine | Nouveau code | Route |
|---|---|---|
| `config.php`, `db.php` | `app/config.py`, `app/db.py` | — |
| `admin_auth.php`, `admin_auth_check.php` | `app/auth.py`, `routes/admin.py` | `POST /api/admin_auth_check` |
| `student_helpers.php` | `app/student_helpers.py` | — |
| `verifier.php` | `routes/students.py` | `POST /api/verifier` |
| `inscrire.php` | `routes/students.py` | `POST /api/inscrire` |
| `demander_modification.php` | `routes/students.py` | `POST /api/demander_modification` |
| `update_etudiant.php` | `routes/students.py` | `POST /api/update_etudiant` |
| `update_telephone.php` | `routes/students.py` | `POST /api/update_telephone` |
| `get_etudiants.php` | `routes/admin.py` | `GET /api/get_etudiants` |
| `admin_modifications.php` | `routes/admin.py` | `GET`/`POST /api/admin_modifications` |
| `import_etudiants.php` | `routes/admin.py` | `POST /api/import_etudiants` |
| `pointage.php` | `routes/attendance.py` | `GET`/`POST /api/pointage` |

Choix techniques :

- `app/errors.py` : toutes les réponses gardent le contrat `{status, message}` ;
  dates en `AAAA-MM-JJ`, horodatages en `AAAA-MM-JJ HH:MM:SS` (le frontend fait
  `heure_pointage.slice(11, 16)`).
- `app/main.py` : CORS `*` et `Cache-Control: no-store` sur `/api/*`, comme en PHP ;
  le dossier `frontend/` est servi à la racine par FastAPI (même origine, donc
  pas de configuration CORS côté navigateur).
- `app/db.py` : pool `psycopg_pool`, `prepare_threshold=None` (pooler Neon /
  PgBouncer), vérification des connexions (Neon coupe les connexions inactives).
- Configuration : `DATABASE_URL` + `UPB_ADMIN_TOKEN` dans `backend/.env`
  (ignoré par git, modèle dans `.env.example`) ; les anciennes variables
  `UPB_DB_HOST/NAME/USER/PASS/DRIVER` disparaissent, ainsi que le mot de passe de
  repli en clair qui figurait dans `config.php`.

Écarts par rapport à `transcription_python/` (corrections de bugs, PHP pris
comme référence) :

1. **Import CSV** : en cas d'erreur de validation, les lignes valides déjà
   insérées étaient *validées* (COMMIT) alors que le message annonçait « Aucune
   ligne importée ». Désormais la transaction est annulée (comme le
   `rollBack()` PHP). Couvert par `test_failed_import_rolls_back_valid_rows`.
2. **Demande de modification** : `import json` manquant (la route plantait au
   premier appel valide).
3. `update_telephone` / `update_etudiant` : plus de `404` sur 0 ligne modifiée
   (le PHP répondait `success`) ; `sexe` n'est plus forcé en majuscules dans
   `update_etudiant` (le PHP ne le faisait pas).
4. `pointage` : le message de succès échappe le matricule en HTML (`escHtml`
   PHP) et `taux_presence` s'affiche `50 %` et non `50.0 %`, comme PHP.
5. `get_etudiants` : une erreur sur les statistiques n'empêche plus d'afficher
   la liste (comportement PHP).
6. Import CSV : `matricules` renvoyés dans l'ordre d'insertion (PHP
   `array_keys`), au lieu d'un tri alphabétique.

Limitation connue : une méthode HTTP non prévue sur une route existante renvoie
`404` JSON (et non `405`), car le service statique du frontend est monté à la
racine. Le frontend n'est pas concerné.

## Étape 3 — Schéma de base et migrations

- `backend/migrations/00_schema_base.sql` (nouveau) : le schéma initial
  (`etudiant`, `filiere`, `niveau`, `candidat_concours`) n'existait nulle part
  dans le dépôt et la base Neon était vide. Le fichier est **déduit des
  requêtes SQL du code** (colonnes lues/écrites, longueurs de champs validées
  par l'API). Seule contrainte ajoutée : `etudiant.matricule UNIQUE`
  (le code le traite comme identifiant). **À comparer avec le schéma d'origine si
  vous en disposez.**
- `backend/scripts/migrate.py` : applique `migrations/*.sql` dans l'ordre sur
  `DATABASE_URL` (migrations idempotentes). Exécuté sur Neon : tables créées.

## Étape 4 — Frontend (`frontend/`)

Seul changement de contenu : les appels `fetch('xxx.php')` deviennent
`fetch('/api/xxx')` (index.html : 3, admin.html : 10, scanner.html : 2) et le
message « Vérifiez que le serveur PHP… » devient « Vérifiez que le serveur… ».
Logique, styles et scripts inchangés.

## Étape 5 — Suppression de PHP et des fichiers obsolètes

Supprimés (`git rm`, récupérables via l'historique git) :

- les 14 fichiers `*.php` ;
- `transcription_python/` (ébauche Flask : son contenu est repris et corrigé
  dans `backend/`) et `transcription_python.zip` ;
- `openapi.yaml` : décrivait les chemins `.php` ; FastAPI génère désormais le
  contrat à jour sur `/openapi.json` et la documentation sur `/docs`.

`README.md` réécrit pour FastAPI/Neon (installation, configuration, API,
migrations, tests). Ajout d'un `.gitignore` (`.env`, `.venv`, `__pycache__`).

## Étape 6 — Vérification

- `python -m pytest -q` (dans `backend/`) : **24 tests passent**, dont un
  parcours complet sur la base Neon (import, vérification, inscription,
  demandes approuvées/refusées, recalcul de matricule, conflit de matricule,
  pointage et double pointage, rollback d'import).
- Démarrage réel avec `uvicorn` : `/`, `/admin.html`, `/scanner.html`, `/docs`
  répondent `200` ; `POST /api/admin_auth_check` renvoie `401` sans jeton et
  `200` avec ; `GET /api/get_etudiants` et `GET /api/pointage` interrogent
  Neon avec succès.
- Recherche de traces : aucun fichier `.php` ni mention de PHP/Flask/XAMPP/PDO
  dans le code, le frontend ou le README (seul ce journal les cite, pour
  documenter la migration). Un test (`test_no_php_reference_in_frontend`)
  vérifie l'absence de `.php` dans les pages servies.
- Correctif découvert pendant la vérification : la connexion à la base était
  empruntée au pool *avant* la validation du corps de requête, donc une latence
  Neon faisait échouer même une requête invalide (500 au lieu de 400, test
  intermittent). `app/db.py` fournit maintenant une classe `Database` à
  connexion paresseuse : la base n'est sollicitée qu'au premier accès.

## Points à valider de votre côté

1. `00_schema_base.sql` est déduit du code : comparez-le avec le schéma de la
   base d'origine si elle existe encore (types, contraintes, index).
2. Le jeton admin généré est dans `backend/.env` (ignoré par git). L'URL Neon
   (mot de passe inclus) y figure aussi : comme elle a été partagée en clair
   dans la conversation, envisagez de renouveler le mot de passe depuis le
   tableau de bord Neon.
3. Les modifications ne sont pas commitées.

## Étape 7 — Documentation OpenAPI/Swagger et guide d'intégration

- `backend/app/docs.py` (nouveau) : modèles Pydantic **uniquement documentaires**
  (requêtes, réponses, erreurs). Les routes continuent de lire le JSON à la main
  (messages d'erreur d'origine conservés) : aucun changement de comportement.
- Chaque route porte `tags`, `summary`, `description`, corps de requête, réponses
  de succès et d'erreur (`400/401/404/409/422/500/503`) ; `update_telephone`
  est marquée `deprecated`. Le détail de la route `import_etudiants` (CSV) est
  décrit dans sa description.
- `app/auth.py` : `require_admin` s'appuie sur `HTTPBearer` (schéma `AdminBearer`),
  ce qui affiche le cadenas et le bouton **Authorize** dans Swagger. Comportement
  inchangé : `401` jeton absent/invalide, `503` jeton non configuré.
- `app/main.py` : description générale (conventions, authentification) et
  tags ; `/docs`, `/redoc`, `/openapi.json`.
- `scripts/export_openapi.py` : exporte le contrat vers `docs/openapi.json`.
- Nouveau test `test_openapi_documents_every_route` (toutes les routes
  documentées, schéma Bearer, routes publiques sans sécurité).
- `docs/GUIDE_INTEGRATION.md` (nouveau) : appel `POST /api/verifier`, matricule,
  procédure de comparaison côté serveur, exemples Python / Node / curl, codes
  d'erreur, ce que la vérification prouve ou non, données personnelles,
  check-list.
- Connexion Neon sans pooler : ajoutée comme `DATABASE_URL_DIRECT` dans
  `backend/.env` ; `scripts/migrate.py` l'utilise en priorité (DDL), l'API
  continue d'utiliser l'URL poolée. Migrations rejouées avec succès via la
  connexion directe.

## Étape 8 — Parcours « matricule inconnu » et documentation protégée

**Logique candidat (portail `index.html`)**
- Matricule trouvé : affichage du dossier, corrections possibles (`/api/demander_modification`,
  validées par l'admin), puis inscription — comportement existant, conservé.
- Matricule non trouvé : message `Matricule non reconnus dans la base I-UPB.`
  (ancien : « Matricule et date de naissance non reconnus dans la base UPB. »,
  modifié dans `routes/students.py`, le guide et les tests) puis affichage du
  même formulaire, vide, pour renseigner ses informations ; le bouton devient
  « Envoyer mon dossier pour validation » et « Confirmer mon inscription » est masqué
  tant que le dossier n'existe pas.
- `migrations/04_etudiant_creation_demande.sql` : table `etudiant_creation_demande`
  (matricule saisi, données JSON, statut `EN_ATTENTE/APPROUVEE/REFUSEE`, une seule
  demande en attente par matricule). Appliquée sur Neon.
- `POST /api/demander_creation` (public) : mêmes validations que la demande de
  correction (extraites dans `_validated_fields`) ; nom, prénoms et date de naissance
  obligatoires ; `409` si le matricule saisi ou le matricule calculé existe déjà, ou
  si une demande est déjà en attente.
- `GET/POST /api/admin_creations` (admin) : liste et approbation/refus. L'approbation
  crée l'étudiant (filière/niveau créés au besoin) avec le matricule **calculé**
  (3 lettres du nom + initiale du prénom + jjmmaa + 0001, règle du reste de
  l'application), pas celui saisi par le candidat ; le matricule créé est renvoyé et
  affiché à l'admin. Un conflit de matricule renvoie `422` et annule tout.
- `admin.html` : nouveau bloc « Demandes d'ajout de dossier » dans la section des
  demandes (même présentation, boutons Approuver / Refuser).

**Documentation en ligne soumise à autorisation**
- `/docs`, `/redoc` et `/openapi.json` passent par `app/docs_access.py` : HTTP Basic,
  nom d'utilisateur libre, mot de passe = `UPB_DOCS_PASSWORD` (remis aux intégrateurs
  autorisés) ou `UPB_ADMIN_TOKEN`. Le mot de passe de la doc ne donne aucun droit sur
  l'API d'administration (testé). `503` si rien n'est configuré.
- `errors.py` : les en-têtes des erreurs HTTP (ici `WWW-Authenticate`) sont conservés.

**Vérification** : 31 tests passent (parcours complet inconnu → demande → approbation →
vérification → inscription ; refus puis nouvelle demande ; accès à la doc ; libellé).
Syntaxe JavaScript de `index.html` et `admin.html` validée avec `node --check`.
Les écrans n'ont pas été manipulés dans un navigateur.

## Étape 9 — Connexion administrateur par identifiant + mot de passe

Remplace le jeton fixe partagé `UPB_ADMIN_TOKEN` (supprimé partout).

- `migrations/05_administrateur.sql` : table `administrateur` (identifiant unique sans
  distinction de casse, `mot_de_passe_hash`, `actif`, `derniere_connexion`). Appliquée sur Neon.
- `app/security.py` (nouveau, bibliothèque standard uniquement) :
  - mots de passe hachés **scrypt** avec sel aléatoire (jamais stockés en clair) ;
    un identifiant inconnu coûte le même temps de calcul (pas de fuite par le délai) ;
  - jetons de session signés HMAC-SHA256 (`UPB_SECRET_KEY`, 32 caractères minimum),
    expirant (`UPB_SESSION_HOURS`, 8 h par défaut) ;
  - limitation des essais : 5 échecs → blocage 15 min (par adresse IP et par identifiant,
    en mémoire, par processus).
- `POST /api/admin_login` (`routes/session.py`, public) : `{identifiant, password}` →
  `{token, expires_in, identifiant}` ; `401` message unique « Identifiant ou mot de passe
  incorrect. » (compte inconnu, désactivé ou mauvais mot de passe), `429` si bloqué.
- `app/auth.py` : `require_admin` valide le jeton de session ; l'en-tête
  `Authorization: Bearer <jeton>` reste le même pour toutes les routes admin et le
  pointage, donc aucun changement dans leur appel. `503` si `UPB_SECRET_KEY` manque.
- `scripts/create_admin.py` : `python -m scripts.create_admin IDENTIFIANT` crée un compte
  ou change son mot de passe (saisi au clavier, 10 caractères minimum) ;
  `--desactiver` désactive un compte.
- Documentation `/docs` : accessible avec `UPB_DOCS_PASSWORD` ou avec l'identifiant +
  mot de passe d'un administrateur (HTTP Basic).
- Frontend : `admin.html` affiche un formulaire Identifiant / Mot de passe ; le jeton de
  session est conservé dans `sessionStorage` (comme avant, utilisé aussi par
  `scanner.html`) ; une réponse `401` en cours d'usage ramène à l'écran de connexion avec
  « Votre session a expiré ».
- `.env` : `UPB_ADMIN_TOKEN` remplacé par une `UPB_SECRET_KEY` générée.

Limites connues : les sessions sont sans état, donc désactiver un compte n'invalide pas
une session déjà ouverte avant son expiration (changer `UPB_SECRET_KEY` déconnecte tout le
monde) ; le compteur d'essais est local à chaque processus et, derrière un proxy, l'adresse
IP vue est celle du proxy (le blocage par identifiant reste effectif).

## Étape 10 — Fin de l'inscription depuis le portail ; inscription via le site officiel

Les inscriptions au concours se feront sur un site dédié ; l'étudiant vérifié depuis ce
site doit être marqué inscrit dans notre base.

- **Portail (`index.html`)** : bouton « Confirmer mon inscription au concours », fonction
  `inscrireCandidat`, bloc de résultat (numéro + QR code) et script `qrcodejs` supprimés ;
  textes ajustés (« Mon dossier »). Le portail ne sert plus qu'à consulter son dossier, le
  corriger (validation admin) ou soumettre un dossier si le matricule est inconnu.
- **`POST /api/inscrire` supprimée** : publique et sans contrôle, elle aurait permis à
  n'importe qui de marquer n'importe quel étudiant inscrit.
- **`POST /api/partenaire/verifier`** (`routes/partner.py`) : réservée aux partenaires
  (en-tête `X-API-Key`). Retrouve l'étudiant, crée sa ligne `candidat_concours` s'il n'est pas
  encore inscrit (statut `EN_ATTENTE`, comme l'ancienne inscription, donc compatible avec les
  statistiques et le pointage), et renvoie son identité et `inscription`
  (`numero_candidat`, `statut`, `deja_inscrit`). Idempotent. `numero_candidat` facultatif
  (numéro du site officiel), sinon généré. Même message 404 que le portail.
- **Clés d'API** : `migrations/06_partenaire.sql` (table `partenaire`, seule l'empreinte
  SHA-256 est stockée) et `python -m scripts.create_partner NOM` (clé affichée une seule
  fois ; relancer la commande la renouvelle ; `--desactiver` la révoque).
- Guide d'intégration : section dédiée au site officiel ; README, description Swagger et
  contrat `docs/openapi.json` mis à jour (tag « Partenaires », schéma `PartnerApiKey`).

## Étape 11 — « Tout approuver » et espacements de la page admin

**Approuver toutes les demandes d'un coup**
- `POST /api/admin_approve_all` (admin) : approuve toutes les demandes de correction et
  d'ajout de dossier en attente. Chaque demande est traitée indépendamment (sa propre
  transaction) : une demande invalide (ex. conflit de matricule) reste en attente et est
  listée dans `echecs`, sans bloquer les autres. Réponse : `modifications` et `creations`,
  chacun `{approuvees, echecs: [{id, message}]}`.
- `routes/admin.py` : la logique d'approbation/refus de `admin_modifications` et
  `admin_creations` est extraite dans `_decide_modification` / `_decide_creation`,
  réutilisées par les routes unitaires (comportement inchangé) et par le traitement groupé.
- `admin.html` : section « Demandes en attente » avec résumé (« N demandes en attente »),
  boutons **Actualiser** et **Tout approuver (N)** (confirmation avant exécution, bouton
  désactivé s'il n'y a rien à approuver) et bilan affiché sous les boutons, avec la liste des
  demandes restées en attente et la raison.
- Test `test_approve_all_pending_requests` : comme l'endpoint agit sur toute la base, le test
  **s'abstient s'il existe des demandes réelles en attente** (pour ne jamais les approuver).

**Espacements (marges et paddings), `admin.html`**
Bloc CSS « Espacements » en fin de feuille de style, rythme vertical sur base de 4 px et
gouttière horizontale constante (24 px, 16 px sur mobile) :
- en-têtes de bloc : plus d'air au-dessus, sous le surtitre et sous le sous-titre ; largeur
  de ligne limitée pour la lecture ;
- import CSV : champs, boutons et aide séparés (filet pointillé), marge basse ;
- demandes : cartes redessinées (nom et matricule à gauche, date en pastille à droite,
  champs modifiés en grille de petites cartes libellé/valeur, filet puis boutons) ; groupes
  « Corrections » et « Ajouts de dossier » séparés avec compteur ; états vides encadrés.
  Le sous-titre « Demandes d'ajout de dossier » ajouté à l'étape 8 n'avait aucun padding
  (collé au bord) : corrigé ;
- pointage : outils sur une ligne, tuiles de statistiques et état vide plus aérés ;
- mobile : gouttières réduites, boutons des demandes en pleine largeur, grille en une colonne.
Vérifié par captures (Edge headless, API simulée) à 1400 px et à 400 px : aucun débordement.

## Étape 12 — Journal d'activité et onglet « Journal »

Traçabilité de toutes les actions menées sur et à travers le système.

- `migrations/07_journal_activite.sql` : table `journal_activite` (date, type d'acteur, acteur,
  catégorie, action, statut `SUCCES/ECHEC`, cible, message, détails JSONB, code HTTP, IP,
  méthode, chemin, navigateur) et index. Appliquée sur Neon.
- `app/audit.py` (nouveau) : un middleware (`main.py`) ouvre un contexte d'audit par requête et
  journalise **un événement par action** une fois la réponse produite (statut déduit du code
  HTTP). Les routes complètent l'événement (`audit.annotate` : cible, noms de champs, compteurs),
  les gestionnaires d'erreurs y ajoutent le motif, l'authentification y inscrit l'acteur
  (administrateur ou partenaire). Les réponses `401` sur toute route `/api` non listée sont
  journalisées (`ACCES_REFUSE`). Le traitement groupé journalise une ligne par demande + une synthèse.
- Garanties : écriture via un pool **dédié** (`db.audit_connection`), donc indépendante de la
  transaction de la requête et sans risque d'interblocage ; un échec d'écriture n'empêche jamais
  l'action (testé) ; aucun mot de passe, jeton ni clé d'API, et seulement les *noms* des champs
  modifiés (testé : les valeurs ne figurent pas dans le journal).
- API (`routes/journal.py`) : `GET /api/admin_journal` (filtres : recherche libre, catégorie,
  action, type d'acteur, résultat, période ; pagination ; statistiques 24 h ; listes d'actions
  et de catégories), `GET /api/admin_journal/export` (CSV UTF-8 `;`, neutralisation des formules,
  20 000 lignes max, l'export est lui-même tracé), `POST /api/admin_logout` (trace la
  déconnexion) et `POST /api/admin_export` (trace les exports Excel/PDF faits dans le navigateur).
- `admin.html` : nouvel onglet **Journal** (synthèse, filtres, tableau avec détails dépliables,
  pagination, export CSV) ; la déconnexion et les exports Excel/PDF existants sont désormais
  signalés au serveur.
- Tests : `tests/conftest.py` désactive l'audit par défaut (`UPB_AUDIT_ENABLED=0`) pour ne pas
  écrire dans Neon ; `tests/test_journal.py` l'active et nettoie ses lignes (adresse
  « testclient »).
- Choix assumé : les consultations réussies de listes par un administrateur ne sont pas
  journalisées (volume) ; la vérification publique d'un matricule l'est, car elle expose des
  données personnelles.
- Limites : pas de purge automatique ; derrière un proxy, l'IP est celle du proxy ; une
  base injoignable pendant l'écriture fait perdre l'événement (consigné dans les logs).

## Étape 13 — Gestion des intégrations (sites externes)

Page d'administration (onglet **Intégrations**) pour gérer de bout en bout les sites externes qui
vérifient des étudiants avec notre API.

- `migrations/08_integrations.sql` : la fiche `partenaire` reçoit `permission` (VERIFICATION /
  INSCRIPTION), `champs` (données transmises), `ips_autorisees`, `limite_par_minute`, `description`,
  `site_url`, `contact` ; nouvelle table `partenaire_cle` (plusieurs clés par partenaire, préfixe
  affichable, expiration, révocation, dernière utilisation, compteur). Les partenaires existants
  conservent leur comportement (permission INSCRIPTION, toutes les données) et leur clé est reprise.
- `app/auth.py` : `require_partner` contrôle dans l'ordre clé connue, non révoquée, non expirée,
  partenaire actif, IP autorisée, quota par minute (`security.RateLimiter`). Le motif précis du
  refus n'est donné qu'au journal ; l'appelant reçoit 401 générique (403 IP, 429 quota).
- `POST /api/partenaire/verifier` : **le matricule suffit** ; réponse `verifie`, `data` limitée aux
  champs autorisés (le matricule seul est possible), `inscription` (`null` sans permission
  d'inscription). Facultatif : `nom`, `prenoms`, `date_naissance` renvoient une `correspondance`
  champ par champ sans révéler les valeurs. `numero_candidat` sans permission d'inscription : 403.
- `routes/partners.py` : `GET/POST /api/admin_partenaires`, `/modifier`, `/cles` (générer / révoquer),
  `/supprimer`. La valeur d'une clé n'est renvoyée qu'à la création/génération ; seule son empreinte
  SHA-256 est stockée ; maximum 10 clés actives ; le nom est immuable (il identifie l'intégration
  dans le journal). Statistiques par intégration tirées du journal d'activité.
- Journal : nouvelles actions `PARTENAIRE_CREE / MODIFIE / ACTIVE / DESACTIVE / CLE_GENEREE /
  CLE_REVOQUEE / SUPPRIME` et `VERIFICATION_PARTENAIRE` ; aucune clé n'y figure (testé).
- `admin.html` : onglet Intégrations (synthèse, cartes par intégration avec autorisations, activité
  et tableau des clés), fenêtres de création/modification, de nouvelle clé et d'affichage unique de
  la clé (copie, exemple d'appel), bouton « Matricule seul », lien vers l'activité dans le journal.
- `scripts/create_partner.py` : conservé pour le dépannage, adapté aux nouvelles tables.
- `docs/GUIDE_INTEGRATION.md` réécrit autour de la route à clé : vérification par le seul matricule,
  correspondance facultative, permissions, codes, exemples, rotation des clés.
- Tests : `tests/test_partners.py` (création, validations, permissions, champs, IP, quota, rotation,
  expiration, révocation, désactivation, suppression, matricule seul, correspondance, journal).

## Étape 14 — Préparation au déploiement (Netlify + Render + UptimeRobot)

Guide : `docs/GUIDE_DEPLOIEMENT.md` (sans `netlify.toml` ni `render.yaml`, tout se règle dans les tableaux de bord).
Trois adaptations du code, nécessaires parce que pages et API sont désormais sur deux hôtes :

- **URL de l'API configurable** : `frontend/config.js` (`window.UPB_API_URL`, vide = même hôte, donc le
  développement local est inchangé) ; `index.html`, `admin.html` et `scanner.html` préfixent leurs appels
  `/api/…` avec cette URL. Test : plus aucun `fetch('/api…')` direct.
- **CORS restreignable** : variable `UPB_CORS_ORIGINS` (liste d'origines, vide = toutes).
- **Adresse IP derrière proxy** (`app/netinfo.py`, `UPB_TRUSTED_PROXY_HOPS`) : l'en-tête
  `X-Forwarded-For` est lu depuis la **droite** (les entrées ajoutées par nos proxys), pas depuis le début
  que le client peut falsifier. Utilisée par le journal, la limitation des essais de connexion (sans cela,
  tous les visiteurs partageraient l'IP du proxy, donc un blocage commun) et la liste d'IP des intégrations.
- **Point de santé** `GET|HEAD /api/sante` pour UptimeRobot : sans authentification, hors documentation,
  **sans accès à la base** (Neon peut se mettre en veille) ; `?base=1` vérifie aussi la base (diagnostic).
- Vérifié : dépendances installées et 42 tests hors base réussis sous **Python 3.12.10** (version conseillée
  pour Render) ; démarrage avec la commande Render (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`),
  GET et HEAD sur `/api/sante`, CORS (origine autorisée / refusée) et lecture de l'IP via `X-Forwarded-For`.

## Étape 15 — Import : nom et prénoms seuls obligatoires, alphabet français uniquement

- **Colonnes** : seules `nom` et `prenoms` sont obligatoires (`CSV_REQUIRED`) ; `date_naissance`,
  `filiere`, `niveau` et les autres sont facultatives. La date, si elle est fournie mais invalide, reste une
  erreur (elle n'est pas ignorée en silence). Même règle pour les candidats (`/api/demander_creation` :
  date facultative ; `/api/demander_modification` : une date vide est « non renseignée »).
- **Noms étrangers** : interprété comme *alphabet*, pas comme origine. `app/student_rules.validate_name`
  n'accepte que A-Z, les lettres accentuées du français, espaces, tirets et apostrophes (au moins
  2 lettres, début et fin par une lettre). Les écritures non latines, chiffres, symboles et emojis sont
  refusés, avec la liste des caractères refusés dans le message. Appliqué à l'import CSV, aux demandes
  de création et de correction, et re-contrôlé à l'approbation. Les données déjà en base ne sont pas
  modifiées. Aucun filtre sur l'origine ou la nationalité d'un nom (injuste et impossible à faire de façon fiable).
- **Matricule sans date** : le matricule dépend de la date de naissance ; sans elle il devient
  `NOM(3 lettres) + initiale + 000000 + numéro` (premier numéro libre de 0001 à 9999) ; il est recalculé
  avec la date quand elle est ajoutée ; le matricule sans date déjà valable est conservé si le nom change
  sans changer de préfixe.
- **Doublons** : sans date, impossible de distinguer deux personnes ; même nom et prénoms (accents, casse et
  espaces ignorés) avec dates identiques, ou date absente d'un côté, = doublon refusé (à l'import, à la
  demande de création et à l'approbation). Les homonymes ne passent que s'ils ont des dates connues différentes.
- **Modèle d'import** : fichier statique `frontend/modele_import_etudiants.csv` (UTF-8 avec BOM, fins de ligne
  Windows pour Excel, séparateur `;`, colonnes obligatoires en premier). Les lignes commençant par `#` sont
  ignorées par l'importeur : le modèle contient des lignes d'aide et des exemples commentés qu'on ne peut donc
  pas importer par accident. Remplace le modèle généré par la page. Test de bout en bout avec le vrai importeur.
- `admin.html` et `index.html` : textes d'aide mis à jour ; contrôle des noms côté page (le serveur reste
  l'autorité) ; nom et prénoms marqués obligatoires, date « facultative ».

## Étape 16 — Le matricule est fourni, jamais calculé ; modèle d'import avec matricule

Correction de l'étape 15 : le matricule n'est pas auto-formé, il est saisi (étudiant) ou importé (fichier).

- **Import** (`/api/import_etudiants`) : colonne `matricule` prise en charge (alias `numero_matricule`, `n_matricule`,
  `no_matricule`) ; **seules `nom` et `prenoms` restent obligatoires**, tout le reste est facultatif, matricule
  compris. Matricule fourni : contrôlé (format `validate_matricule` : 3-4 lettres + `jjmmaa` possible + 4 chiffres ;
  unicité en base et dans le fichier) puis importé tel quel, mis en majuscules. Matricule absent : étudiant créé
  **sans matricule** (compté dans `sans_matricule`). Les lignes sans matricule sont contrôlées contre les doublons
  par nom (même nom et prénoms, date identique ou inconnue d'un côté) ; les lignes avec matricule ne le sont que
  sur l'unicité du matricule (les homonymes sont permis).
- **Matricule jamais recalculé** : supprimés le matricule « sans date » (`000000`), le recalcul à l'approbation
  d'une correction et le recalcul quand la date change (`update_etudiant`). Une correction de nom, prénoms ou
  date ne modifie pas le matricule.
- **Candidats** : `/api/demander_creation` exige un matricule valide (format) ; l'approbation crée le dossier avec
  **le matricule saisi** (re-contrôlé, `422` s'il existe déjà), plus le matricule « calculé ».
- **Étudiant sans matricule** : `migrations/09_matricule_facultatif.sql` (colonne `etudiant.matricule` nullable, la
  contrainte UNIQUE est conservée). L'administrateur l'attribue ensuite dans la liste (champ « À renseigner ») :
  `POST /api/update_etudiant` accepte `matricule` (format, unicité `409`) pour un étudiant qui n'en a pas ; un
  matricule déjà attribué n'est plus modifiable (`422`) *[remplacé à l'étape 18 : l'administrateur peut désormais modifier le matricule]*. Sans matricule, l'étudiant est introuvable par le portail
  et les intégrations.
- **Modèle** `frontend/modele_import_etudiants.csv` : colonne `matricule` ajoutée, exemples avec et sans matricule.
- **Tests** : `tests/test_import_rules.py` réécrit : chaque colonne facultative absente / vide / renseignée,
  tout vide sauf nom et prénoms, colonnes `nom` et `prenoms` obligatoires, formats de matricule valides et
  invalides, doublons, attribution ultérieure, modèle de bout en bout, saisies des candidats, corrections qui ne
  changent jamais le matricule. Autres tests adaptés au matricule fourni.

## Étape 17 — Import en erreur 500 en production : schéma de base en retard

- **Cause** : la base de production n'avait pas la migration `09_matricule_facultatif.sql` (ajoutée après le guide de
  déploiement) : `etudiant.matricule` y était encore NOT NULL, donc toute ligne importée **sans matricule** échouait
  (500) alors que l'import **avec matricule** passait. Reproduit dans un schéma isolé (copie des tables avant la
  migration 09, supprimé ensuite) : avec matricule 201, sans matricule 500. Les tests locaux utilisent la base de
  développement, déjà à jour.
- **Message clair** : les erreurs de schéma (table ou colonne absente, NULL refusé) renvoient désormais « La base de
  données n'est pas à jour : appliquez les migrations (python -m scripts.migrate) sur cette base. » à l'import et dans le
  gestionnaire d'erreurs global, au lieu d'un message générique.
- **Détection** : `db.schema_problems` vérifie que l'objet créé par chaque migration (01 à 09) existe ;
  `GET /api/sante?base=1` répond 503 avec `migrations_manquantes` si la base est en retard ; au démarrage, le journal de
  Render écrit « SCHÉMA DE BASE OBSOLÈTE » (contrôle en tâche de fond, sans retarder le démarrage).
- **Correctif de production** : appliquer les migrations sur la base de production (guide de déploiement, étape 1).

## Étape 18 — Administration complète des étudiants ; la vérification n'inscrit plus

- **Modifier tous les champs** (`POST /api/update_etudiant`) : nom, prénoms (alphabet français), **matricule** (désormais
  modifiable par l'admin : format + unicité `409`, jamais recalculé), date de naissance, filière, niveau (créés au besoin),
  téléphone, e-mail, sexe, allergies, et numéro de candidat d'un étudiant inscrit. Une chaîne vide efface un champ
  facultatif (nom et prénoms restent obligatoires) ; une date vide l'efface (avant : ignorée).
- **Créer un étudiant** (`POST /api/admin_etudiants`) : seuls nom et prénoms obligatoires ; matricule facultatif (format +
  unicité) ; sans matricule, un homonyme exact est refusé (`409`). Bouton « Nouvel étudiant » dans la page admin.
- **Désinscrire** (`POST /api/admin_desinscrire`) : supprime la ligne `candidat_concours` ; le dossier et les pointages sont
  conservés ; `404` si non inscrit. Bouton « Désinscrire » dans la liste (avec confirmation). Un bouton « Modifier » ouvre
  la fenêtre d'édition de tous les champs ; l'enregistrement rapide de la liste (téléphone, e-mail, sexe, naissance) reste.
- **La vérification n'inscrit plus jamais** : `POST /api/partenaire/verifier` est en lecture seule pour toutes les clés
  (`inscription` indique seulement l'état actuel ; `numero_candidat` y est refusé, `400`). Nouvel appel
  `POST /api/partenaire/inscrire` (permission « Vérification et inscription », `403` sinon), idempotent, que le site
  officiel appelle **uniquement quand le compte est effectivement créé**. **Changement incompatible** pour une
  intégration qui comptait sur `verifier` pour inscrire : elle doit appeler `inscrire`.
- Journal : `ETUDIANT_CREE`, `ETUDIANT_DESINSCRIT` ; la vérification partenaire est toujours `VERIFICATION_PARTENAIRE`,
  l'inscription `INSCRIPTION_CONCOURS`. Textes de l'onglet Intégrations et guide d'intégration mis à jour.
- Tests : `tests/test_admin_students.py` (création, validations, doublons, édition et effacement de chaque champ,
  conflits, numéro de candidat, désinscription, réinscription par le site, traçabilité) ; tests partenaires,
  journal et flux de base adaptés.

## Étape 19 — Listes déroulantes fermées : filière et niveau

- **Valeurs autorisées** (source unique : `FILIERES` et `NIVEAUX` dans `backend/app/student_rules.py`) :
  filière = ASSRI, RIT, MIAGE, SEG, SEA, SJAP, 3EA ; niveau = Licence 1, Licence 2, Licence 3. La comparaison ignore
  la casse et les espaces (`assri` → `ASSRI`, `licence3` → `Licence 3`) et enregistre toujours la forme canonique.
  Vide = « non précisé ». Aucun changement de schéma (pas de nouvelle migration).
- **Nouvelle route publique** `GET /api/referentiels` : renvoie `filieres` et `niveaux` ; les pages l'utilisent
  pour construire leurs listes (valeurs de repli identiques dans le code des pages si l'API tarde).
- **Validation partout** (erreur 422 « Filière non reconnue … Valeurs autorisées : … ») : import CSV (erreur de
  ligne, tout-ou-rien), demande de création et demande de correction candidat, approbation admin de ces demandes,
  création d'étudiant par l'admin, modification par l'admin.
- **Données historiques** : un étudiant dont la filière ou le niveau est hors liste n'est ni bloqué ni effacé. Une
  valeur hors liste renvoyée *inchangée* par un formulaire est tolérée ; la remplacer par une autre valeur hors liste
  est refusé.
- **Frontend** : nouveau composant `frontend/upb-select.js` (liste déroulante au design du site, clavier et
  lecteurs d'écran : combobox/listbox, flèches, Début/Fin, Échap), qui remplace les champs libres du portail
  (`index.html`) et de la fenêtre étudiant de l'administration (`admin.html`). Le `<select>` natif reste dans le DOM.
  Modèle d'import et aide de l'import mis à jour avec les valeurs de la liste.
- Tests : `tests/test_referentiels.py` (règles, route, pages et composant, modèle, import, flux candidat et admin,
  valeurs historiques). **À faire côté déploiement** : redéployer le backend (Render) et le frontend (Netlify).

## Étape 20 — Suppression d'un étudiant, comparaison des noms, e-mails de décision

- **Suppression définitive** : `POST /api/admin_supprimer_etudiant` (`{id}`) et bouton « Supprimer » dans la liste des
  étudiants (confirmation). Efface le dossier et ses dépendances (inscription au concours, pointages, demandes de
  correction, par `ON DELETE CASCADE` ; les demandes d'ajout déjà traitées sont conservées, sans lien). `404` si
  introuvable. Journal : `ETUDIANT_SUPPRIME` (matricule, était inscrit, nombre de pointages supprimés ; jamais de données
  personnelles). Distinct de « Désinscrire », qui garde le dossier.
- **Comparaison des noms** (`student_rules.py`) : les noms et prénoms sont déjà en base pour tous les étudiants. Une
  demande est comparée à ces noms (accents, casse, tirets ignorés ; ordre et nombre de prénoms indifférents ; score de
  ressemblance, seuil de tolérance 0,80 : une faute légère est « proche », pas « différent »).
  - *Demande d'ajout de dossier* : la liste admin indique la `correspondance` trouvée. Si un dossier **sans matricule**
    porte ce nom (faute légère tolérée), l'approbation **complète ce dossier** (matricule, nom et prénoms saisis fusionnés,
    autres champs renseignés seulement s'ils sont fournis) au lieu de créer un doublon (`fusion: true`). Un homonyme
    déjà pourvu d'un matricule n'est jamais écrasé : l'admin est averti (faute possible dans le matricule) et l'approbation
    crée un nouveau dossier. Aucun nom retrouvé : avertissement, création d'un nouveau dossier.
  - *Demande de correction touchant au nom ou aux prénoms* : `verification_noms` (`score`, `niveau`) ; avertissement si
    le nom proposé ne ressemble pas à celui en base.
  - Pages : encadré vert/orange sous chaque demande.
- **E-mails de décision via Resend** (`app/mailer.py`) : approbation ou refus (demande d'ajout ou de correction, y compris
  « Tout approuver ») envoie un e-mail à l'étudiant (adresse de la demande, sinon celle du dossier). Expéditeur affiché :
  « INCUBATEUR UPB <noreply@synapstic.tech> » (`UPB_MAIL_NAME`, `UPB_MAIL_FROM`) ; ni Cc, ni Cci, ni Reply-To : aucune autre
  adresse n'est visible. Une décision unique attend le résultat de l'envoi : `mail` vaut `envoye`, `echec` (avec
  `mail_erreur`, ex. domaine non vérifié), `sans_email` ou `non_configure`, et la notification de la page l'affiche.
  « Tout approuver » envoie en arrière-plan (`mis_en_file`) et un échec est écrit dans le journal (`EMAIL_ECHEC`). Un échec
  ne bloque jamais la décision. Variables : `RESEND_API_KEY`, `UPB_MAIL_FROM`, `UPB_MAIL_NAME`, `UPB_MAIL_ENABLED`.
  Message mis en forme aux couleurs du site (en-tête bleu marine et liseré or, logo, pastille « Approuvé » / « Non retenu »,
  bloc matricule, pied de page), compatible messageries (tableaux, styles en ligne, texte brut joint). Le logo doit être
  une image hébergée en https : `UPB_MAIL_LOGO_URL`, sinon `logo-upb.png` du premier site de `UPB_CORS_ORIGINS` ; sans
  adresse, l'en-tête n'a que le nom.
  Vérifier la configuration : `python scripts/test_mail.py adresse@exemple.ci` (depuis `backend/`). **À faire** : créer la
  clé Resend, vérifier
  le domaine `synapstic.tech` (SPF/DKIM) et ajouter `RESEND_API_KEY` sur Render.
- Tests : `tests/test_mail_and_names.py` (ressemblance, fusion, homonymes, e-mails simulés, confidentialité de
  l'expéditeur, panne Resend) et suppression dans `tests/test_admin_students.py`. Les tests n'envoient jamais d'e-mail réel.

## Étape 21 — Commentaire de refus et bouton de mise à jour dans l'e-mail de refus

- **Commentaire** : en refusant une demande (correction ou ajout de dossier), l'administrateur ouvre une fenêtre « Refuser la
  demande » avec un champ **Commentaire pour l'étudiant** (facultatif, 500 caractères, compteur). Annuler n'envoie rien.
  API : champ `commentaire` de `POST /api/admin_modifications` et `/api/admin_creations` (lu seulement pour `reject` ; ignoré à
  l'approbation) ; texte uniquement, espaces et caractères de contrôle nettoyés, `422` si plus de 500 caractères ou non texte.
  Il est conservé avec la demande (colonne `commentaire`) et joint à l'e-mail de refus (bloc « Motif du refus »). Le journal
  n'enregistre que sa longueur (`commentaire_longueur`), jamais son contenu.
- **Bouton dans l'e-mail de refus** : « Mettre à jour mes informations », vers `UPB_PORTAL_URL`, sinon la racine du premier site
  de `UPB_CORS_ORIGINS` (https ; `http://localhost` et `127.0.0.1` tolérés pour les essais locaux). **Lien de repli sans
  configuration** : le site d'où l'administrateur refuse la demande (en-tête `Origin`, sinon `Referer`), puisque les pages
  d'administration et du portail sont servies par le même site ; accepté s'il est en https (ou `localhost`) et, quand
  `UPB_CORS_ORIGINS` est défini, s'il en fait partie. Ordre : `UPB_PORTAL_URL`, origine de l'administrateur, premier site de
  `UPB_CORS_ORIGINS`. Le bouton n'est omis que si aucune de ces adresses n'est utilisable (avertissement dans les logs).
  Définir `UPB_PORTAL_URL` reste recommandé en production. Le lien ne contient aucune donnée personnelle.
  La version texte de l'e-mail reprend le motif et le lien. L'e-mail d'approbation n'a ni bouton ni motif.
- **Migration 10** (`10_commentaire_refus.sql`) : colonne `commentaire` sur `etudiant_modification` et
  `etudiant_creation_demande` (idempotente ; détectée par la sonde de schéma). **À appliquer sur la base de production**
  (`python -m scripts.migrate`) avant de déployer le code, sinon `/api/sante?base=1` signale la migration manquante.
- Tests : `tests/test_mail_and_names.py` (mise en forme, motif, bouton, validation, stockage, approbation sans motif) et
  `tests/test_api_basic.py` (sonde de schéma).

## Étape 22 — Accusé de réception : l'étudiant est informé du délai de 12 heures

- Après `POST /api/demander_modification` et `/api/demander_creation`, la réponse `201` complète le message :
  - adresse e-mail connue (celle de la demande, ou pour une correction celle déjà au dossier) : « Vous serez informé(e) de la
    décision par e-mail dans un délai de 12 heures. » ;
  - aucune adresse : « Aucune adresse e-mail n'est renseignée : vous ne serez pas prévenu(e) de la décision. Revenez vérifier
    votre matricule pour la connaître. » (le délai n'est alors pas promis).
- Nouveaux champs de réponse : `notifie_par_email` (booléen) et `delai_heures` (12, constante `DECISION_DELAY_HOURS` dans
  `routes/students.py`). Le portail affiche ce message dans l'encadré de confirmation et dans la bulle de Gé ; une indication
  sous le champ « Adresse e-mail » invite à la renseigner.
- Le délai est un engagement affiché à l'étudiant : il suppose que les demandes soient traitées dans les 12 h. Aucun changement
  de schéma.
- Tests : `tests/test_mail_and_names.py`.

### Complément (étape 21) — erreurs de décision explicites

- Une erreur de base lors d'une décision (`/api/admin_creations`, `/api/admin_modifications`), d'une création, d'une
  désinscription, d'une suppression ou du chargement des demandes renvoie `500` ; si la cause est un schéma en retard
  (colonne ou table absente, ex. migration 10 non appliquée), le message ajoute « appliquez les migrations (python -m
  scripts.migrate) sur cette base ». Toute autre erreur reste générique.
- Diagnostic en production : `https://<api>/api/sante?base=1` liste les `migrations_manquantes` ; le détail de l'erreur est dans
  les logs Render (« Traitement de demande de création UPB »).

## Étape 23 — L'import CSV met à jour les étudiants existants

- Un fichier peut être **réimporté** : les étudiants déjà en base ne bloquent plus l'import. Chaque ligne est rapprochée :
  - **avec matricule** : si le matricule existe, l'étudiant est mis à jour (le nom et les prénoms du fichier doivent lui
    ressembler, sinon erreur « le matricule X appartient à un autre étudiant » : protège d'un matricule mal attribué ; une faute
    d'orthographe légère corrige le nom en base). Si le matricule est nouveau, il **complète** l'étudiant de même nom qui n'a pas
    encore de matricule (aucun doublon) ; à défaut, un étudiant est créé (les homonymes avec matricules différents restent
    permis) ;
  - **sans matricule** : rapprochement par **nom et prénoms** (accents, casse, espaces ignorés) et par la date de naissance
    quand elle est connue des deux côtés (deux dates différentes = deux personnes). Un seul correspondant : mise à jour (son nom
    n'est pas réécrit) ; aucun : création sans matricule ; plusieurs : erreur « précisez le matricule ou la date de
    naissance ». Le même nom sans matricule répété dans le fichier reste une erreur.
- **Une cellule vide conserve la valeur en base** ; seules les cellules renseignées et différentes sont écrites.
- Toujours **tout ou rien** : une erreur annule aussi les mises à jour du fichier. Doublon de matricule dans le fichier : erreur.
- Réponse : `imported` (créés), `updated`, `unchanged`, `sans_matricule`, `matricules` (créés ou mis à jour) ; message
  « Import terminé : x créé(s), y mis à jour, z inchangé(s). ». Le journal garde les volumes (`importes`, `mis_a_jour`,
  `inchanges`). Aide de la page et du modèle CSV mises à jour. Aucun changement de schéma.
- Remplace le comportement des étapes 15–16 « homonyme exact sans matricule refusé comme doublon ».
- Tests : `tests/test_import_update.py` ; `test_import_rules.py` et `test_db_flow.py` adaptés.
