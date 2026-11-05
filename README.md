# API Concours Génie UPB

Cette API PHP permet de rechercher des étudiants, de les inscrire au concours,
de proposer des corrections, de gérer les dossiers et d'enregistrer les
présences. Le contrat lisible par les outils est [openapi.yaml](./openapi.yaml)
(OpenAPI 3.0.3) ; importez-le dans Swagger UI, Postman ou Insomnia pour explorer
les opérations et leurs schémas.

## URL et formats

L'URL de base dépend du déploiement. Avec XAMPP en local, elle est généralement
`http://localhost/upb-api`. En production, remplacez-la par l'URL HTTPS réelle
et mettez à jour l'entrée `servers` dans `openapi.yaml`.

- Les corps JSON sont envoyés avec `Content-Type: application/json`.
- Les réponses JSON sont encodées en UTF-8.
- L'import CSV utilise `multipart/form-data`.
- Les routes acceptent les requêtes preflight CORS (`OPTIONS`). Le code actuel
  autorise toutes les origines (`Access-Control-Allow-Origin: *`).
- Les dates sont au format `AAAA-MM-JJ`, sauf les dates de demande de correction
  qui acceptent aussi `JJ/MM/AAAA` et `JJ-MM-AAAA`.

## Authentification administrateur

Les routes d'administration exigent l'en-tête :

```http
Authorization: Bearer VOTRE_JETON_ADMIN
```

`VOTRE_JETON_ADMIN` est un espace réservé : remplacez-le par la valeur exacte
configurée sur le serveur. Le serveur lit le jeton depuis la variable
d'environnement `UPB_ADMIN_TOKEN` ; il doit contenir au moins 10 caractères.
Les requêtes d'administration reçoivent `401` si le jeton fourni est absent ou
invalide, et `503` si le jeton n'est pas configuré sur le serveur.
`POST /admin_auth_check.php` permet de vérifier la configuration.

Générez un jeton aléatoire sur le serveur ou dans un environnement sûr, par
exemple avec OpenSSL :

```sh
openssl rand -hex 32
```

Configurez la valeur produite comme `UPB_ADMIN_TOKEN` dans la configuration
privée de l'hébergement (ou dans l'environnement du serveur web), puis
redémarrez/rechargez le service web si nécessaire. Pour un test ponctuel en
PowerShell, vous pouvez définir la variable pour la session courante :

```powershell
$env:UPB_ADMIN_TOKEN = "COLLER_ICI_LE_JETON_GENERE"
```

Cette commande PowerShell ne configure que le processus courant et ses
processus enfants ; utilisez la configuration de l'hébergement pour un
déploiement persistant. Le jeton réel n'est pas inclus dans cette
documentation : créez-en un nouveau pour chaque environnement et transmettez-le
aux intégrateurs autorisés par un canal sécurisé.

Ne placez jamais ce jeton dans le code JavaScript d'un navigateur ou une
application cliente distribuée. Pour une intégration web, faites transiter les
appels d'administration par un serveur que vous contrôlez.

## Endpoints

`{baseUrl}` ci-dessous désigne l'URL de base configurée au déploiement.

| Méthode et chemin | Authentification | Fonction |
|---|---|---|
| `POST /admin_auth_check.php` | Administrateur | Vérifier le jeton. |
| `POST /verifier.php` | Aucune actuellement | Rechercher un dossier avec `matricule`. La réponse contient aussi coordonnées et allergies/affections. |
| `POST /inscrire.php` | Aucune actuellement | Inscrire un étudiant existant au concours. |
| `GET /get_etudiants.php` | Administrateur | Rechercher et paginer les dossiers étudiants. |
| `POST /update_etudiant.php` | Administrateur | Modifier téléphone, e-mail, sexe, allergies/affections et/ou date de naissance. |
| `POST /update_telephone.php` | Administrateur | Ancienne route de modification du téléphone ; préférer `update_etudiant.php`. |
| `POST /demander_modification.php` | Aucune actuellement | Soumettre une correction à valider par un administrateur. |
| `GET /admin_modifications.php` | Administrateur | Lister les demandes en attente. |
| `POST /admin_modifications.php` | Administrateur | Approuver ou refuser une demande. |
| `POST /import_etudiants.php` | Administrateur | Importer un fichier CSV. |
| `GET /pointage.php` | Administrateur | Lire les présences d'une date. |
| `POST /pointage.php` | Administrateur | Enregistrer une présence par matricule. |

### Exemples d'appels

Inscrire un étudiant :

```sh
curl -X POST "{baseUrl}/inscrire.php" \
  -H "Content-Type: application/json" \
  -d '{"matricule":"KOUA1703980001"}'
```

Lister les dossiers avec authentification, recherche et pagination :

```sh
curl -G "{baseUrl}/get_etudiants.php" \
  -H "Authorization: Bearer VOTRE_JETON_ADMIN" \
  --data-urlencode "q=KOUA" \
  --data-urlencode "page=1" \
  --data-urlencode "per_page=20"
```

Proposer une correction (aucune authentification n'est actuellement requise) :

```sh
curl -X POST "{baseUrl}/demander_modification.php" \
  -H "Content-Type: application/json" \
  -d '{"matricule":"KOUA1703980001","telephone":"0701020304","email":"nom@exemple.ci"}'
```

Enregistrer un pointage :

```sh
curl -X POST "{baseUrl}/pointage.php" \
  -H "Authorization: Bearer VOTRE_JETON_ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"matricule":"KOUA1703980001","date":"2026-06-10","jour_libelle":"Jour 1"}'
```

## Contrats et règles métier

- **Recherche étudiants** : `GET /get_etudiants.php` accepte `page` (1 par
  défaut), `per_page` (20 par défaut, maximum 100) et `q` (recherche partielle
  sur le matricule, le nom, les prénoms ou la filière). La réponse comprend
  `data`, `total`, `page`, `per_page`, `total_pages` et les statistiques globales
  dans `stats`.
- **Inscription** : un matricule doit déjà exister. Une première inscription
  retourne `201`, un matricule inconnu `404` et un étudiant déjà inscrit `409`
  (avec son numéro de candidat).
- **Modification directe** : `update_etudiant.php` attend `id` et au moins un
  champ modifiable. Une date de naissance valide recalcule le matricule ; un
  conflit retourne `409`. Une chaîne vide efface un champ texte facultatif.
- **Demande de correction** : le corps doit contenir `matricule` et au moins un
  des champs `nom`, `prenoms`, `date_naissance`, `filiere`, `niveau`,
  `telephone`, `email`, `sexe`, `allergies_affection`. La demande reste en
  attente jusqu'à son approbation. Une seconde demande en attente pour le même
  dossier retourne `409`.
- **Traitement des demandes** : le `POST /admin_modifications.php` attend
  `{"id": 123, "action": "approve"}` ou `{"id": 123, "action": "reject"}`.
- **Import CSV** : envoyer le fichier dans le champ `fichier`. Taille maximale :
  5 Mo ; maximum : 5 000 lignes. Les colonnes requises sont `nom`, `prenoms`,
  `date_naissance`, `filiere`, `niveau`. Les colonnes facultatives sont
  `telephone`, `email`, `sexe`, `allergies_affection`. Les séparateurs virgule
  et point-virgule sont reconnus. Une erreur de validation annule tout l'import
  (`422`, aucune ligne n'est importée). Les filières et niveaux inconnus sont
  créés lors de l'import.
- **Pointage** : le GET accepte `?date=AAAA-MM-JJ` (aujourd'hui par défaut).
  Le POST attend `matricule`, avec `date` et `jour_libelle` facultatifs. Un
  étudiant ne peut être pointé qu'une fois par date (`409` en cas de doublon).

## Réponses et erreurs

Les opérations renvoient généralement un objet JSON avec `status` égal à
`success` ou `error`, et un champ `message`. Les codes à traiter dépendent de
l'opération ; les principaux sont `200` (succès), `201` (création), `400`
(requête invalide), `401` (authentification), `404` (introuvable), `409`
(conflit), `422` (validation) et `500` (erreur serveur). Le détail exact des
réponses de chaque opération figure dans le contrat OpenAPI.

Exemple d'erreur :

```json
{
  "status": "error",
  "message": "Matricule non reconnu."
}
```

## Configuration serveur

La connexion à la base peut être configurée via les variables d'environnement
suivantes :

| Variable | Rôle |
|---|---|
| `UPB_DB_HOST` | Hôte PostgreSQL |
| `UPB_DB_NAME` | Nom de la base |
| `UPB_DB_USER` | Utilisateur de base |
| `UPB_DB_PASS` | Mot de passe de base |
| `UPB_DB_DRIVER` | Pilote PDO (par défaut `pgsql`) |
| `UPB_ADMIN_TOKEN` | Jeton d'accès aux opérations administrateur |

## Transférer et installer l'API sur un autre serveur

L'API est le code PHP : elle doit être installée sur un serveur web PHP. La
base PostgreSQL peut se trouver sur ce même serveur ou sur un serveur distinct,
à condition que le serveur PHP puisse s'y connecter. Copier uniquement les
fichiers SQL ou la base de données ne suffit pas pour rendre les endpoints
accessibles.

### Prérequis

- Un serveur web configuré pour exécuter PHP (Apache/XAMPP ou équivalent).
- PHP 7.1 au minimum pour la syntaxe utilisée ; privilégier une version PHP
  encore maintenue.
- Les extensions PHP `PDO`, `PDO_PGSQL` et `iconv` activées. `PDO_PGSQL` est
  nécessaire pour la connexion PostgreSQL ; `iconv` est utilisé notamment
  pendant l'import CSV.
- PostgreSQL accessible depuis le serveur PHP.
- Une base de données contenant le schéma principal de l'application.

### 1. Préparer les fichiers à transmettre

Transmettre les fichiers PHP de l'API, `student_helpers.php`, le dossier
contenant les éventuels fichiers de l'interface si celle-ci est souhaitée,
les scripts SQL disponibles (`01_pointage.sql`, `02_etudiant_contact.sql`,
`03_inscription_migrations.sql`), ainsi que `README.md` et `openapi.yaml`.

Ne pas inclure dans l'archive de code de mot de passe réel, de jeton
administrateur, de sauvegarde contenant des données personnelles, du dossier
`.git` ou des fichiers temporaires. Une sauvegarde de base ne doit être
transmise séparément que si le destinataire est autorisé à accéder aux données,
par un canal sécurisé.

### 2. Préparer ou restaurer PostgreSQL

**Pour transférer une base existante**, créer une sauvegarde PostgreSQL depuis
la machine qui héberge la base, puis la restaurer sur le serveur destinataire.
Exemple avec les outils PostgreSQL dans PowerShell :

```powershell
pg_dump -h localhost -U postgres -d upb_concours --no-owner --no-acl -f .\upb_concours.sql
createdb -h SERVEUR_CIBLE -U postgres upb_concours
psql -h SERVEUR_CIBLE -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\upb_concours.sql
```

Remplacer les hôtes, utilisateurs et noms de base par les valeurs réelles. La
commande peut demander le mot de passe ; ne l'inscrire ni dans le fichier SQL,
ni dans une commande enregistrée ou partagée. La sauvegarde contient les
données de la base : la protéger et la supprimer des emplacements temporaires
après transfert sécurisé.

**Pour une installation neuve**, les scripts SQL présents dans ce dépôt ne
constituent pas le schéma initial complet : `01_pointage.sql` référence déjà la
table `etudiant`. Il faut d'abord créer/restaurer le schéma principal et les
données de référence requis par l'application (notamment `etudiant`, `filiere`,
`niveau` et, pour l'inscription, `candidat_concours`). Le dépôt ne contient pas
de script autonome qui crée toutes ces tables. Demander le schéma initial ou
une sauvegarde autorisée à la personne qui gère la base source.

Après restauration d'une sauvegarde complète à jour, vérifier les migrations
déjà appliquées avant d'exécuter d'autres scripts. Pour une base de départ qui
possède le schéma principal mais pas ces ajouts, appliquer les scripts dans cet
ordre depuis le répertoire où ils se trouvent :

```powershell
psql -h SERVEUR_CIBLE -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\01_pointage.sql
psql -h SERVEUR_CIBLE -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\02_etudiant_contact.sql
psql -h SERVEUR_CIBLE -U postgres -d upb_concours -v ON_ERROR_STOP=1 -f .\03_inscription_migrations.sql
```

Ces scripts ajoutent le pointage, des champs de contact et les demandes de
modification. Les fichiers `02_etudiant_contact.sql` et
`03_inscription_migrations.sql` ajoutent certains champs en commun ; ils
utilisent `IF NOT EXISTS` pour ces ajouts. Ils ne remplacent pas le schéma
principal.

### 3. Installer le code PHP

Copier les fichiers PHP de l'API dans le répertoire servi par le serveur web,
par exemple `htdocs\upb-api` sous XAMPP. Le dossier doit être joignable par
l'URL qui sera communiquée aux intégrateurs. Si seule l'API est nécessaire,
l'interface HTML/CSS/JavaScript n'est pas obligatoire.

Sur le serveur PHP, configurer les variables d'environnement :

```text
UPB_DB_HOST=hôte PostgreSQL accessible depuis le serveur PHP
UPB_DB_NAME=upb_concours
UPB_DB_USER=utilisateur dédié à l'API
UPB_DB_PASS=mot de passe de cet utilisateur
UPB_DB_DRIVER=pgsql
UPB_ADMIN_TOKEN=jeton aléatoire généré pour cet environnement
```

Créer de préférence un utilisateur PostgreSQL dédié à l'application, avec les
droits nécessaires sur les tables et séquences utilisées par l'API. Restreindre
la connexion PostgreSQL à l'hôte PHP si la base est distante. Dans un
environnement de production, fournir les secrets via la configuration protégée
du serveur (et non un dépôt ou un fichier public). La configuration PHP contient
des valeurs de repli destinées au développement : définir explicitement toutes
les variables ci-dessus en production et ne pas utiliser ces valeurs de repli.

### 4. Vérifier la connexion et les endpoints

1. Vérifier depuis le serveur PHP que le pilote `pdo_pgsql` est actif et que
   l'hôte/base/utilisateur configurés sont corrects.
2. Envoyer une requête POST à `{baseUrl}/admin_auth_check.php` avec l'en-tête
   `Authorization: Bearer VOTRE_JETON_ADMIN`. Une réponse `200` confirme la
   configuration du jeton ; `503` indique qu'il manque côté serveur et `401`
   qu'il est invalide.
3. Tester un endpoint administrateur avec ce même en-tête et un endpoint public
   avec des données de test autorisées.
4. Importer `openapi.yaml` dans Swagger UI, Postman ou Insomnia. Remplacer
   l'URL `localhost` dans `servers` par l'URL publique réelle avant de partager
   le contrat.
5. Activer HTTPS avant tout échange de données réelles. Vérifier aussi les
   règles CORS si le système intégrateur appelle l'API depuis un navigateur.

Communiquer aux intégrateurs l'URL HTTPS, le contrat OpenAPI et les consignes
d'authentification nécessaires. Transmettre le jeton administrateur par un
canal secret distinct, uniquement aux intégrateurs autorisés ; ne jamais
l'inclure dans un exemple, le fichier OpenAPI, une application cliente ou un
dépôt de code.

## Export et réutilisation des données

Le projet ne fournit pas actuellement d'endpoint dédié pour exporter un CSV ou
un fichier de sauvegarde. Un système peut récupérer les étudiants via
`GET /get_etudiants.php` en parcourant les pages `page`/`per_page` ; cette route
requiert le jeton administrateur et retourne des données personnelles. Pour
une sauvegarde ou une migration complète, utiliser les outils PostgreSQL
`pg_dump`/`psql` avec l'accord et les protections appropriés, plutôt qu'un
endpoint HTTP. Ne pas partager les exports de données sans autorisation.

## Sécurité et données personnelles

Les réponses de `verifier.php` et de la liste administrateur incluent des
données personnelles, dont le champ `allergies_affection`. Le code actuel
n'exige pas de jeton sur `verifier.php` et autorise les demandes de correction
sans authentification. Utilisez HTTPS, limitez l'accès réseau en production et
évaluez les contrôles d'accès nécessaires avant d'exposer ces routes. Le jeton
administrateur protège les routes admin, mais n'est pas un mécanisme
d'authentification des étudiants.
