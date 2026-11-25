"""Routes d'administration : contrôle du jeton, liste des étudiants, demandes, import CSV."""
import csv
import io
import logging
import re
import unicodedata
from math import ceil
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, File, Query, UploadFile

from .. import config
from .. import audit, mailer
from ..auth import require_admin
from ..db import Database, get_db
from ..db import schema_hint as db_schema_hint
from ..errors import ApiError, respond
from ..student_helpers import (
    fix_encoding,
    lookup_reference_id,
    parse_student_date,
    valid_email,
)
from .. import docs as d
from ..student_helpers import byte_length
from ..student_rules import (
    best_name_match,
    load_students_for_matching,
    name_level,
    name_similarity,
    REFERENCE_VALIDATORS,
    index_add,
    index_find,
    load_name_index,
    validate_filiere,
    validate_matricule,
    validate_name,
    validate_niveau,
)
from .common import json_body, json_object, positive_int

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])

MODIFICATION_FIELDS = {
    "nom",
    "prenoms",
    "date_naissance",
    "filiere",
    "niveau",
    "telephone",
    "email",
    "sexe",
    "allergies_affection",
}


@router.post(
    "/api/admin_auth_check",
    tags=["Administration"],
    summary="Vérifier le jeton administrateur",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(401, 503)},
)
def check_admin_auth():
    return respond(200, {"status": "success", "message": "Accès administrateur autorisé."})


def _query_int(value: str | None, default: int) -> int:
    """Entier de la query string : défaut si absent, 0 si non numérique."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return 0


@router.get(
    "/api/get_etudiants",
    tags=["Administration"],
    summary="Lister et rechercher les étudiants",
    description="Recherche partielle (insensible à la casse) sur matricule, nom, prénoms "
    "et filière, triée par nom, avec statistiques globales.",
    response_model=None,
    responses={200: {"model": d.StudentsListResponse}, **d.errors(401, 500, 503)},
)
def get_students(
    page: str | None = Query(default=None, description="Numéro de page (1 par défaut)."),
    per_page: str | None = Query(default=None, description="Taille de page (20 par défaut, 100 maximum)."),
    q: str = Query(default="", description="Recherche partielle."),
    db: Database = Depends(get_db),
):
    page_number = max(1, _query_int(page, 1))
    size = min(config.max_per_page(), max(1, _query_int(per_page, config.default_per_page())))
    offset = (page_number - 1) * size
    search = q.strip()

    base = """
        FROM etudiant e
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
    """
    where = ""
    parameters: tuple = ()
    if search:
        where = """
            WHERE e.matricule ILIKE %s
               OR e.nom ILIKE %s
               OR e.prenoms ILIKE %s
               OR f.libelle ILIKE %s
        """
        pattern = f"%{search}%"
        parameters = (pattern, pattern, pattern, pattern)

    stats: dict[str, Any] = {
        "total": 0,
        "inscrits": 0,
        "telephones": 0,
        "emails": 0,
        "filieres": [],
        "niveaux": [],
    }
    try:
        with db.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS total {base} {where}", parameters)
            total = cursor.fetchone()["total"]
            cursor.execute(
                f"""
                SELECT e.id, e.matricule, e.nom, e.prenoms, e.date_naissance,
                       e.telephone, e.email, e.sexe, e.allergies_affection,
                       f.libelle AS filiere, n.libelle AS niveau,
                       c.numero_candidat, c.statut
                {base} {where}
                ORDER BY e.nom ASC
                LIMIT %s OFFSET %s
                """,
                (*parameters, size, offset),
            )
            students = cursor.fetchall()
    except psycopg.Error as error:
        logger.exception("Lecture des étudiants UPB")
        raise ApiError(500, str(error).strip() or "Erreur de base de données.")

    for student in students:
        for field, value in student.items():
            student[field] = fix_encoding(value)

    # Statistiques globales : indépendantes de la pagination et de la recherche.
    # Sans statistiques, la liste reste affichable.
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM etudiant")
            stats["total"] = cursor.fetchone()["total"]
            cursor.execute("SELECT COUNT(DISTINCT etudiant_id) AS total FROM candidat_concours")
            stats["inscrits"] = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM etudiant "
                "WHERE telephone IS NOT NULL AND TRIM(telephone) <> ''"
            )
            stats["telephones"] = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM etudiant "
                "WHERE email IS NOT NULL AND TRIM(email) <> ''"
            )
            stats["emails"] = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COALESCE(f.libelle, 'Non renseignée') AS libelle, COUNT(e.id) AS total
                FROM etudiant e
                LEFT JOIN filiere f ON e.filiere_id = f.id
                GROUP BY COALESCE(f.libelle, 'Non renseignée')
                ORDER BY total DESC
                """
            )
            stats["filieres"] = cursor.fetchall()
            cursor.execute(
                """
                SELECT COALESCE(n.libelle, 'Non renseigné') AS libelle, COUNT(e.id) AS total
                FROM etudiant e
                LEFT JOIN niveau n ON e.niveau_id = n.id
                GROUP BY COALESCE(n.libelle, 'Non renseigné')
                ORDER BY total DESC
                """
            )
            stats["niveaux"] = cursor.fetchall()
        for key in ("filieres", "niveaux"):
            for row in stats[key]:
                row["libelle"] = fix_encoding(row["libelle"])
    except psycopg.Error:
        logger.exception("Statistiques étudiants UPB")

    return respond(
        200,
        {
            "status": "success",
            "data": students,
            "total": total,
            "page": page_number,
            "per_page": size,
            "total_pages": ceil(total / size),
            "stats": stats,
        },
    )


@router.get(
    "/api/admin_modifications",
    tags=["Administration"],
    summary="Lister les demandes de correction en attente",
    response_model=None,
    responses={200: {"model": d.ModificationsListResponse}, **d.errors(401, 500, 503)},
)
def list_modifications(db: Database = Depends(get_db)):
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.id, m.etudiant_id, m.modifications, m.created_at,
                       e.matricule, e.nom, e.prenoms, e.date_naissance
                FROM etudiant_modification m
                JOIN etudiant e ON e.id = m.etudiant_id
                WHERE m.statut = 'EN_ATTENTE'
                ORDER BY m.created_at ASC
                """
            )
            requests = cursor.fetchall()
    except psycopg.Error:
        logger.exception("Lecture des demandes UPB")
        raise ApiError(500, "Impossible de charger les demandes.")
    for request in requests:
        request["verification_noms"] = _name_check(request)
    return respond(200, {"status": "success", "data": requests})


def _name_check(request: dict) -> dict | None:
    """Pour une correction qui touche au nom ou aux prénoms : ressemblance avec ce qui est déjà en base."""
    changes = request.get("modifications") or {}
    if not isinstance(changes, dict) or not ({"nom", "prenoms"} & set(changes)):
        return None
    nom = changes.get("nom") or request["nom"]
    prenoms = changes.get("prenoms") or request["prenoms"]
    score = name_similarity(str(nom), str(prenoms), request["nom"], request["prenoms"])
    return {"score": score, "niveau": name_level(score)}


COMMENT_MAX = 500


def _refusal_comment(data: dict) -> str | None:
    """Motif de refus saisi par l'administrateur (facultatif, 500 caractères au plus, texte brut)."""
    value = data.get("commentaire")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(422, "Le commentaire doit être un texte.")
    cleaned = re.sub(r"[^\S\n]+", " ", re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)).strip()
    if len(cleaned) > COMMENT_MAX:
        raise ApiError(422, f"Le commentaire est trop long ({COMMENT_MAX} caractères au maximum).")
    return cleaned or None


def _send_notice(result: dict, wait: bool = True) -> str:
    """Envoie l'e-mail de décision (après l'enregistrement) ; `result` reçoit `mail` (+ `mail_erreur` en cas d'échec)."""
    state, error = mailer.notify(result.pop("notice", None), wait=wait)
    result["mail"] = state
    if error:
        result["mail_erreur"] = error
    return state


def _decide_modification(db: Database, request_id: int, action: str, comment: str | None = None) -> dict:
    """Approuve ou refuse une demande de correction (transaction propre). `comment` : motif du refus."""
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, etudiant_id, modifications
                    FROM etudiant_modification
                    WHERE id = %s AND statut = 'EN_ATTENTE'
                    FOR UPDATE
                    """,
                    (request_id,),
                )
                modification = cursor.fetchone()
                if not modification:
                    raise ApiError(404, "Demande déjà traitée ou introuvable.")

                if action == "reject":
                    cursor.execute(
                        """
                        UPDATE etudiant_modification
                        SET statut = 'REFUSEE', reviewed_at = NOW(), commentaire = %s
                        WHERE id = %s
                        """,
                        (comment, request_id),
                    )
                    cursor.execute(
                        "SELECT matricule, nom, prenoms, email FROM etudiant WHERE id = %s",
                        (modification["etudiant_id"],),
                    )
                    known = cursor.fetchone() or {}
                    proposed = modification["modifications"] if isinstance(modification["modifications"], dict) else {}
                    return {
                        "message": "Demande refusée.",
                        "notice": {
                            "kind": "correction", "approved": False, "comment": comment,
                            "to": known.get("email") or proposed.get("email"),
                            "nom": known.get("nom", ""), "prenoms": known.get("prenoms", ""),
                            "matricule": known.get("matricule"),
                        },
                    }

                changes = modification["modifications"]
                if (
                    not isinstance(changes, dict)
                    or not changes
                    or set(changes) - MODIFICATION_FIELDS
                ):
                    raise ApiError(422, "Contenu de demande invalide.")

                cursor.execute(
                    """
                    SELECT matricule, nom, prenoms, date_naissance, email
                    FROM etudiant WHERE id = %s FOR UPDATE
                    """,
                    (modification["etudiant_id"],),
                )
                student = cursor.fetchone()
                if not student:
                    raise ApiError(422, "Étudiant associé à la demande introuvable.")

                # Le matricule est un identifiant fourni : il n'est jamais recalculé ni modifié ici.
                matricule = student["matricule"]
                for field, label in (("nom", "Nom"), ("prenoms", "Prénoms")):
                    if field in changes:
                        try:
                            changes[field] = validate_name(changes[field], label)
                        except ValueError as error:
                            raise ApiError(422, str(error))
                for field, validator in REFERENCE_VALIDATORS.items():
                    if changes.get(field):
                        try:
                            changes[field] = validator(changes[field])
                        except ValueError as error:
                            raise ApiError(422, str(error))

                for field, column in (("filiere", "filiere_id"), ("niveau", "niveau_id")):
                    if field in changes:
                        label = changes.pop(field)
                        changes[column] = (
                            None if label is None else lookup_reference_id(cursor, field, label)
                        )

                notice_email = changes.get("email") if "email" in changes else None
                notice_nom = changes.get("nom") or student["nom"]
                notice_prenoms = changes.get("prenoms") or student["prenoms"]
                assignments = []
                values = []
                for field, value in changes.items():
                    if field == "date_naissance" and value is not None:
                        value = parse_student_date(str(value))
                        if not value:
                            raise ApiError(422, "Date de naissance invalide.")
                    if field in ("telephone", "email", "sexe", "allergies_affection") and value == "":
                        value = None
                    assignments.append(f"{field} = %s")
                    values.append(value)
                cursor.execute(
                    f"UPDATE etudiant SET {', '.join(assignments)} WHERE id = %s",
                    (*values, modification["etudiant_id"]),
                )
                cursor.execute(
                    """
                    UPDATE etudiant_modification
                    SET statut = 'APPROUVEE', reviewed_at = NOW()
                    WHERE id = %s
                    """,
                    (request_id,),
                )
    except psycopg.Error:
        logger.exception("Traitement de demande UPB")
        raise ApiError(500, "Erreur lors du traitement de la demande.")

    return {
        "message": "Demande approuvée et informations mises à jour.",
        "matricule": matricule,
        "notice": {
            "kind": "correction", "approved": True, "matricule": matricule,
            "to": notice_email or student["email"],
            "nom": notice_nom, "prenoms": notice_prenoms,
        },
    }


@router.post(
    "/api/admin_modifications",
    tags=["Administration"],
    summary="Approuver ou refuser une demande de correction",
    description="`approve` applique les changements ; `reject` la refuse. Un `commentaire` facultatif "
    "(500 caractères) explique le refus : il est joint à l'e-mail envoyé à l'étudiant.",
    response_model=None,
    responses={
        200: {"model": d.ModificationDecisionResponse},
        **d.errors(400, 401, 404, 422, 500, 503),
    },
    openapi_extra=d.request_body(d.ProcessModificationBody, "Demande et décision."),
)
def process_modification(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    request_id = positive_int(data.get("id"))
    action = data.get("action", "")
    if not request_id or action not in ("approve", "reject"):
        raise ApiError(400, "Demande ou action invalide.")
    audit.annotate(
        action="DEMANDE_CORRECTION_APPROUVEE" if action == "approve" else "DEMANDE_CORRECTION_REFUSEE",
        target_type="demande_correction", target=str(request_id),
    )
    comment = _refusal_comment(data) if action == "reject" else None
    result = _decide_modification(db, request_id, action, comment)
    _send_notice(result)
    audit.annotate(
        matricule=result.get("matricule"), mail=result["mail"], mail_erreur=result.get("mail_erreur"),
        commentaire_longueur=len(comment) if comment else None,
    )
    return respond(200, {"status": "success", **result})


CSV_ALIASES = {
    "prenom": "prenoms",
    "date_de_naissance": "date_naissance",
    "date_naiss": "date_naissance",
    "e_mail": "email",
    "numero_matricule": "matricule",
    "n_matricule": "matricule",
    "no_matricule": "matricule",
    "allergies": "allergies_affection",
    "allergie_affection": "allergies_affection",
    "allergies_affections": "allergies_affection",
}
CSV_REQUIRED = {"nom", "prenoms"}


def _normalize_header(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", str(value).strip()).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_value.lower()).strip("_")


@router.post(
    "/api/import_etudiants",
    tags=["Administration"],
    summary="Importer des étudiants depuis un CSV",
    description="Champ multipart `fichier` (.csv, UTF-8, 5 Mo et 5 000 lignes maximum, "
    "séparateur `,` ou `;`). **Seules les colonnes `nom` et `prenoms` sont obligatoires.** Facultatives : "
    "`matricule`, `date_naissance`, `filiere`, `niveau`, `telephone`, `email`, `sexe`, "
    "`allergies_affection`. Le matricule n'est jamais calculé : s'il est fourni, il est importé tel quel "
    "(format 3 ou 4 lettres majuscules + 6 chiffres jjmmaa + 4 chiffres, ex. `KOUA1703980001`, et unique) ; "
    "sinon l'étudiant est créé sans matricule, à renseigner ensuite. Sans matricule, un homonyme exact déjà "
    "présent (même date de naissance, ou date inconnue) est refusé comme doublon. "
    "Les noms et prénoms n'acceptent que les lettres de l'alphabet français, "
    "espaces, tirets et apostrophes (aucune écriture non latine, aucun chiffre ni symbole). "
    "`filiere` : ASSRI, RIT, MIAGE, SEG, SEA, SJAP ou 3EA ; `niveau` : Licence 1, Licence 2 ou Licence 3 "
    "(casse et espaces ignorés) ; toute autre valeur est une erreur de ligne. "
    "Les lignes dont la première cellule commence par `#` sont ignorées (lignes d'aide du modèle). "
    "Tout ou rien : une erreur annule l'import.",
    response_model=None,
    responses={
        201: {"model": d.ImportResponse, "description": "Étudiants importés."},
        **d.errors(400, 401, 500, 503),
        422: {"model": d.ImportErrorResponse, "description": "Fichier ou lignes invalides."},
    },
)
def import_students(
    fichier: UploadFile | None = File(default=None), db: Database = Depends(get_db)
):
    if fichier is None:
        raise ApiError(400, "Sélectionnez un fichier CSV valide.")
    audit.annotate(target_type="fichier", target=fichier.filename)
    too_big = "Le fichier doit être un CSV de 5 Mo maximum."
    if not fichier.filename or not fichier.filename.lower().endswith(".csv"):
        raise ApiError(422, too_big)
    content = fichier.file.read(config.CSV_MAX_BYTES + 1)
    if len(content) > config.CSV_MAX_BYTES:
        raise ApiError(422, too_big)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(422, "Le fichier CSV doit être encodé en UTF-8.")
    if not text:
        raise ApiError(422, "Le fichier CSV est vide.")

    try:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        semicolon_header = next(csv.reader(io.StringIO(first_line), delimiter=";"), [])
        comma_header = next(csv.reader(io.StringIO(first_line), delimiter=","), [])
        delimiter = ";" if len(semicolon_header) >= len(comma_header) else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        headers = next(reader, [])
    except csv.Error:
        raise ApiError(422, "Fichier CSV invalide.")

    headers = [_normalize_header(value) for value in headers]
    headers = [CSV_ALIASES.get(header, header) for header in headers]
    if not CSV_REQUIRED.issubset(headers):
        raise ApiError(422, "Colonnes obligatoires : nom, prenoms.")

    errors: list[str] = []
    seen_matricules: list[str] = []  # ordre d'insertion
    seen_set: set[str] = set()
    reference_cache: dict[str, dict[str, int | None]] = {"filiere": {}, "niveau": {}}
    imported = 0
    without_matricule = 0
    name_index = None  # chargé seulement si une ligne n'a pas de matricule
    try:
        with db.transaction():
            with db.cursor() as cursor:
                for row_number, values in enumerate(reader, start=2):
                    if not any(value.strip() for value in values):
                        continue
                    if values and values[0].lstrip().startswith("#"):
                        continue  # ligne d'aide du modèle d'import
                    if row_number > config.CSV_MAX_ROW_NUMBER:
                        errors.append("Le fichier dépasse la limite de 5 000 lignes.")
                        break
                    row = {
                        key: (values[index].strip() if index < len(values) else "")
                        for index, key in enumerate(headers)
                    }
                    raw_matricule = row.get("matricule", "")
                    nom = row.get("nom", "")
                    prenoms = row.get("prenoms", "")
                    raw_birth = row.get("date_naissance", "")
                    birth_date = parse_student_date(raw_birth) if raw_birth else None
                    filiere = row.get("filiere", "")
                    niveau = row.get("niveau", "")
                    email = row.get("email", "")
                    sexe = row.get("sexe", "").upper()
                    telephone = row.get("telephone", "")
                    allergies = row.get("allergies_affection", "")

                    if not nom or not prenoms:
                        errors.append(f"Ligne {row_number} : le nom et les prénoms sont obligatoires.")
                        continue
                    try:
                        matricule = validate_matricule(raw_matricule) if raw_matricule else None
                        nom = validate_name(nom, "Nom")
                        prenoms = validate_name(prenoms, "Prénoms")
                    except ValueError as error:
                        errors.append(f"Ligne {row_number} : {error}")
                        continue
                    try:
                        filiere = validate_filiere(filiere) or ""
                        niveau = validate_niveau(niveau) or ""
                    except ValueError as error:
                        errors.append(f"Ligne {row_number} : {error}")
                        continue
                    if raw_birth and not birth_date:
                        errors.append(
                            f"Ligne {row_number} : date de naissance invalide "
                            "(AAAA-MM-JJ ou JJ/MM/AAAA, pas de date future)."
                        )
                        continue
                    if not valid_email(email):
                        errors.append(f"Ligne {row_number} : adresse e-mail invalide.")
                        continue
                    if sexe and sexe not in ("M", "F"):
                        errors.append(f"Ligne {row_number} : le sexe doit être M ou F.")
                        continue
                    if matricule:
                        cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule,))
                        if cursor.fetchone() or matricule in seen_set:
                            errors.append(
                                f"Ligne {row_number} : matricule déjà présent ou dupliqué "
                                f"dans le CSV ({matricule})."
                            )
                            continue
                    else:
                        if name_index is None:
                            name_index = load_name_index(cursor)
                        homonym = index_find(name_index, nom, prenoms, birth_date)
                        if homonym is not False:
                            errors.append(
                                f"Ligne {row_number} : doublon, {nom} {prenoms} existe déjà "
                                f"({'matricule ' + homonym if homonym else 'sans matricule'}). "
                                "Renseignez un matricule ou une date de naissance différente "
                                "s'il s'agit d'un homonyme."
                            )
                            continue

                    reference_ids = {}
                    for table, label in (("filiere", filiere), ("niveau", niveau)):
                        if not label.strip():
                            reference_ids[table] = None
                            continue
                        key = label.strip().lower()
                        if key not in reference_cache[table]:
                            reference_cache[table][key] = lookup_reference_id(
                                cursor, table, label
                            )
                        reference_ids[table] = reference_cache[table][key]
                    cursor.execute(
                        """
                        INSERT INTO etudiant
                            (matricule, nom, prenoms, date_naissance, telephone,
                             email, sexe, allergies_affection, filiere_id, niveau_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            matricule,
                            nom,
                            prenoms,
                            birth_date,
                            telephone or None,
                            email or None,
                            sexe or None,
                            allergies or None,
                            reference_ids["filiere"],
                            reference_ids["niveau"],
                        ),
                    )
                    if matricule:
                        seen_matricules.append(matricule)
                        seen_set.add(matricule)
                    else:
                        without_matricule += 1
                    if name_index is not None:
                        index_add(name_index, nom, prenoms, birth_date, matricule)
                    imported += 1

                # Lever une exception ici annule toute la transaction (aucune ligne importée).
                if errors:
                    raise ApiError(
                        422,
                        "Aucune ligne importée : corrigez les erreurs puis réessayez.",
                        errors=errors[:50],
                    )
                if imported == 0:
                    raise ApiError(422, "Le fichier ne contient aucun étudiant à importer.")
    except psycopg.Error as error:
        logger.exception("Import CSV UPB")
        raise ApiError(
            500,
            "Erreur lors de l'import. " + (db_schema_hint(error) or "Vérifiez le format CSV et réessayez."),
        )

    audit.annotate(importes=imported, sans_matricule=without_matricule)
    return respond(
        201,
        {
            "status": "success",
            "message": f"{imported} étudiant(s) importé(s) avec succès."
            + (f" {without_matricule} sans matricule : à renseigner ensuite." if without_matricule else ""),
            "imported": imported,
            "sans_matricule": without_matricule,
            "matricules": seen_matricules,
        },
    )


@router.get(
    "/api/admin_creations",
    tags=["Administration"],
    summary="Lister les demandes d'ajout de dossier en attente",
    response_model=None,
    responses={200: {"model": d.CreationsListResponse}, **d.errors(401, 500, 503)},
)
def list_creations(db: Database = Depends(get_db)):
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, matricule_saisi, donnees, created_at
                FROM etudiant_creation_demande
                WHERE statut = 'EN_ATTENTE'
                ORDER BY created_at ASC
                """
            )
            requests = cursor.fetchall()
            known = load_students_for_matching(cursor) if requests else []
    except psycopg.Error:
        logger.exception("Lecture des demandes de création UPB")
        raise ApiError(500, "Impossible de charger les demandes.")
    for request in requests:
        request["correspondance"] = _creation_match(known, request.get("donnees"))
    return respond(200, {"status": "success", "data": requests})


def _creation_match(known: list[dict], fields) -> dict | None:
    """Dossier déjà en base dont le nom ressemble à celui de la demande (fautes légères tolérées)."""
    if not isinstance(fields, dict) or not fields.get("nom") or not fields.get("prenoms"):
        return None
    found = best_name_match(known, str(fields["nom"]), str(fields["prenoms"]), str(fields.get("date_naissance") or "") or None)
    if not found:
        return None
    row, score = found
    return {
        "etudiant_id": row["id"], "matricule": row["matricule"], "nom": row["nom"], "prenoms": row["prenoms"],
        "score": score, "niveau": name_level(score), "a_completer": not row["matricule"],
    }


def _decide_creation(db: Database, request_id: int, action: str, comment: str | None = None) -> dict:
    """Approuve (crée l'étudiant) ou refuse une demande d'ajout de dossier. `comment` : motif du refus."""
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, matricule_saisi, donnees FROM etudiant_creation_demande
                    WHERE id = %s AND statut = 'EN_ATTENTE'
                    FOR UPDATE
                    """,
                    (request_id,),
                )
                demande = cursor.fetchone()
                if not demande:
                    raise ApiError(404, "Demande déjà traitée ou introuvable.")

                if action == "reject":
                    cursor.execute(
                        """
                        UPDATE etudiant_creation_demande
                        SET statut = 'REFUSEE', reviewed_at = NOW(), commentaire = %s
                        WHERE id = %s
                        """,
                        (comment, request_id),
                    )
                    refused = demande["donnees"] if isinstance(demande["donnees"], dict) else {}
                    return {
                        "message": "Demande refusée.",
                        "notice": {
                            "kind": "creation", "approved": False, "comment": comment, "to": refused.get("email"),
                            "nom": refused.get("nom", ""), "prenoms": refused.get("prenoms", ""),
                            "matricule": demande["matricule_saisi"],
                        },
                    }

                fields = demande["donnees"]
                if (
                    not isinstance(fields, dict)
                    or not (MODIFICATION_FIELDS >= set(fields))
                    or not all(fields.get(k) for k in ("nom", "prenoms"))
                ):
                    raise ApiError(422, "Contenu de demande invalide.")
                birth = fields.get("date_naissance") or None
                try:
                    # Le matricule est celui saisi par le candidat (jamais calculé), re-contrôlé ici.
                    matricule = validate_matricule(demande["matricule_saisi"])
                    nom = validate_name(fields["nom"], "Nom")
                    prenoms = validate_name(fields["prenoms"], "Prénoms")
                    fields["filiere"] = validate_filiere(fields.get("filiere"))
                    fields["niveau"] = validate_niveau(fields.get("niveau"))
                except ValueError as error:
                    raise ApiError(422, str(error))
                cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule,))
                if cursor.fetchone():
                    raise ApiError(422, "Ce matricule existe déjà pour un autre étudiant.")

                values = (
                    fields.get("telephone") or None,
                    fields.get("email") or None,
                    fields.get("sexe") or None,
                    fields.get("allergies_affection") or None,
                    lookup_reference_id(cursor, "filiere", fields.get("filiere")),
                    lookup_reference_id(cursor, "niveau", fields.get("niveau")),
                )
                # Les noms sont déjà en base pour tous les étudiants : si un dossier SANS matricule porte ce nom
                # (fautes légères tolérées), la demande le complète au lieu de créer un doublon ; le nom et les
                # prénoms saisis (version corrigée) sont fusionnés dans ce dossier.
                merged = None
                found = best_name_match(load_students_for_matching(cursor), nom, prenoms, birth, without_matricule=True)
                if found:
                    cursor.execute(
                        "SELECT id, email FROM etudiant WHERE id = %s AND matricule IS NULL FOR UPDATE", (found[0]["id"],)
                    )
                    merged = cursor.fetchone()
                if merged:
                    student_id = merged["id"]
                    cursor.execute(
                        """
                        UPDATE etudiant SET matricule = %s, nom = %s, prenoms = %s,
                            date_naissance = COALESCE(%s, date_naissance), telephone = COALESCE(%s, telephone),
                            email = COALESCE(%s, email), sexe = COALESCE(%s, sexe),
                            allergies_affection = COALESCE(%s, allergies_affection),
                            filiere_id = COALESCE(%s, filiere_id), niveau_id = COALESCE(%s, niveau_id)
                        WHERE id = %s
                        """,
                        (matricule, nom, prenoms, birth, *values, student_id),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO etudiant
                            (matricule, nom, prenoms, date_naissance, telephone, email, sexe,
                             allergies_affection, filiere_id, niveau_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (matricule, nom, prenoms, birth, *values),
                    )
                    student_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    UPDATE etudiant_creation_demande
                    SET statut = 'APPROUVEE', reviewed_at = NOW(), etudiant_id = %s
                    WHERE id = %s
                    """,
                    (student_id, request_id),
                )
    except psycopg.Error:
        logger.exception("Traitement de demande de création UPB")
        raise ApiError(500, "Erreur lors du traitement de la demande.")

    return {
        "message": (
            "Demande approuvée : le dossier existant a été complété (nom retrouvé en base)."
            if merged else "Demande approuvée : le dossier étudiant a été créé."
        ),
        "matricule": matricule,
        "fusion": bool(merged),
        "notice": {
            "kind": "creation", "approved": True, "matricule": matricule,
            "to": fields.get("email") or (merged or {}).get("email"), "nom": nom, "prenoms": prenoms,
        },
    }


@router.post(
    "/api/admin_creations",
    tags=["Administration"],
    summary="Approuver ou refuser une demande d'ajout de dossier",
    description="`approve` crée l'étudiant avec le matricule saisi par le candidat (contrôlé : format "
    "et unicité) ou complète le dossier existant portant ce nom ; `reject` refuse la demande. Un `commentaire` "
    "facultatif (500 caractères) explique le refus : il est joint à l'e-mail envoyé à l'étudiant.",
    response_model=None,
    responses={
        200: {"model": d.CreationDecisionResponse},
        **d.errors(400, 401, 404, 422, 500, 503),
    },
    openapi_extra=d.request_body(d.ProcessModificationBody, "Demande et décision."),
)
def process_creation(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    request_id = positive_int(data.get("id"))
    action = data.get("action", "")
    if not request_id or action not in ("approve", "reject"):
        raise ApiError(400, "Demande ou action invalide.")
    audit.annotate(
        action="DEMANDE_CREATION_APPROUVEE" if action == "approve" else "DEMANDE_CREATION_REFUSEE",
        target_type="demande_creation", target=str(request_id),
    )
    comment = _refusal_comment(data) if action == "reject" else None
    result = _decide_creation(db, request_id, action, comment)
    _send_notice(result)
    audit.annotate(
        matricule=result.get("matricule"), mail=result["mail"], mail_erreur=result.get("mail_erreur"),
        fusion=result.get("fusion"), commentaire_longueur=len(comment) if comment else None,
    )
    return respond(200, {"status": "success", **result})


def _approve_all(db: Database, table: str, decide, kind: str) -> dict:
    """Approuve chaque demande en attente indépendamment ; les échecs sont listés."""
    with db.cursor() as cursor:
        cursor.execute(f"SELECT id FROM {table} WHERE statut = 'EN_ATTENTE' ORDER BY created_at ASC, id ASC")
        ids = [row["id"] for row in cursor.fetchall()]
    approved, failures = 0, []
    for request_id in ids:
        try:
            result = decide(db, request_id, "approve")
            mail = _send_notice(result, wait=False)
            approved += 1
            audit.log(
                f"DEMANDE_{kind}_APPROUVEE", "DEMANDE", target_type=f"demande_{kind.lower()}",
                target=str(request_id), details={"mode": "masse", "matricule": result.get("matricule"), "mail": mail},
            )
        except ApiError as error:
            failures.append({"id": request_id, "message": error.message})
            audit.log(
                f"DEMANDE_{kind}_APPROUVEE", "DEMANDE", status=audit.ECHEC, target_type=f"demande_{kind.lower()}",
                target=str(request_id), message=error.message, details={"mode": "masse"},
            )
    return {"approuvees": approved, "echecs": failures}


@router.post(
    "/api/admin_approve_all",
    tags=["Administration"],
    summary="Approuver toutes les demandes en attente d'un coup",
    description="Approuve toutes les demandes de correction et d'ajout de dossier en attente. "
    "Chaque demande est traitée indépendamment : une demande invalide (ex. conflit de "
    "matricule) est laissée en attente et listée dans `echecs`, sans bloquer les autres.",
    response_model=None,
    responses={200: {"model": d.ApproveAllResponse}, **d.errors(401, 500, 503)},
)
def approve_all(db: Database = Depends(get_db)):
    modifications = _approve_all(db, "etudiant_modification", _decide_modification, "CORRECTION")
    creations = _approve_all(db, "etudiant_creation_demande", _decide_creation, "CREATION")
    total = modifications["approuvees"] + creations["approuvees"]
    failed = len(modifications["echecs"]) + len(creations["echecs"])
    audit.annotate(
        approuvees=total, echecs=failed,
        corrections=modifications["approuvees"], creations=creations["approuvees"],
    )
    message = f"{total} demande(s) approuvée(s)."
    if failed:
        message += f" {failed} demande(s) n'ont pas pu être approuvées et restent en attente."
    return respond(
        200,
        {"status": "success", "message": message, "modifications": modifications, "creations": creations},
    )


STUDENT_SELECT = """
    SELECT e.id, e.matricule, e.nom, e.prenoms, e.date_naissance, e.telephone, e.email, e.sexe,
           e.allergies_affection, f.libelle AS filiere, n.libelle AS niveau, c.numero_candidat, c.statut
    FROM etudiant e
    LEFT JOIN filiere f ON e.filiere_id = f.id
    LEFT JOIN niveau n ON e.niveau_id = n.id
    LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
"""


@router.post(
    "/api/admin_etudiants",
    tags=["Administration"],
    summary="Créer un étudiant",
    description="Création directe d'un dossier par un administrateur. **Seuls `nom` et `prenoms` sont obligatoires.** "
    "Matricule facultatif : s'il est fourni, il est contrôlé (format) et doit être unique (`409`) ; sans matricule, "
    "un homonyme exact déjà présent (même date de naissance, ou date inconnue) est refusé (`409`). "
    "Noms et prénoms : alphabet français uniquement. Filière (ASSRI, RIT, MIAGE, SEG, SEA, SJAP, 3EA) et niveau (Licence 1, 2 ou 3) : listes fermées.",
    response_model=None,
    responses={201: {"model": d.StudentCreatedResponse}, **d.errors(401, 409, 422, 500, 503)},
    openapi_extra=d.request_body(d.StudentCreateBody, "Fiche du nouvel étudiant."),
)
def create_student(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body) or {}
    try:
        nom = validate_name(data.get("nom"), "Nom")
        prenoms = validate_name(data.get("prenoms"), "Prénoms")
        matricule = validate_matricule(data["matricule"]) if data.get("matricule") not in (None, "") else None
    except ValueError as error:
        raise ApiError(422, str(error))

    def text(key: str, maximum: int, label: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or byte_length(value.strip()) > maximum:
            raise ApiError(422, f"{label} invalide ou trop long.")
        return value.strip() or None

    birth = None
    if data.get("date_naissance") not in (None, ""):
        birth = parse_student_date(data["date_naissance"]) if isinstance(data["date_naissance"], str) else None
        if not birth:
            raise ApiError(422, "Date de naissance invalide (AAAA-MM-JJ ou JJ/MM/AAAA, pas de date future).")
    email = text("email", 255, "Adresse e-mail")
    if email and not valid_email(email):
        raise ApiError(422, "Adresse e-mail invalide.")
    sexe = (text("sexe", 1, "Sexe") or "").upper() or None
    if sexe and sexe not in ("M", "F"):
        raise ApiError(422, "Le sexe doit être M ou F.")
    telephone = text("telephone", 40, "Téléphone")
    allergies = text("allergies_affection", 2000, "Allergies / affection")
    try:
        filiere = validate_filiere(text("filiere", 150, "Filière"))
        niveau = validate_niveau(text("niveau", 150, "Niveau"))
    except ValueError as error:
        raise ApiError(422, str(error))
    audit.annotate(
        target_type="matricule" if matricule else "etudiant", target=matricule or f"{nom} {prenoms}",
        sans_matricule=matricule is None,
    )

    try:
        with db.transaction():
            with db.cursor() as cursor:
                if matricule:
                    cursor.execute("SELECT id FROM etudiant WHERE matricule = %s", (matricule,))
                    if cursor.fetchone():
                        raise ApiError(409, "Ce matricule existe déjà pour un autre étudiant.")
                else:
                    homonym = index_find(load_name_index(cursor), nom, prenoms, birth)
                    if homonym is not False:
                        raise ApiError(
                            409,
                            f"Doublon : {nom} {prenoms} existe déjà "
                            f"({'matricule ' + homonym if homonym else 'sans matricule'}). Renseignez un "
                            "matricule ou une date de naissance différente s'il s'agit d'un homonyme.",
                        )
                cursor.execute(
                    """
                    INSERT INTO etudiant (matricule, nom, prenoms, date_naissance, telephone, email, sexe,
                                          allergies_affection, filiere_id, niveau_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                    """,
                    (
                        matricule, nom, prenoms, birth, telephone, email, sexe, allergies,
                        lookup_reference_id(cursor, "filiere", filiere),
                        lookup_reference_id(cursor, "niveau", niveau),
                    ),
                )
                student_id = cursor.fetchone()["id"]
                cursor.execute(STUDENT_SELECT + " WHERE e.id = %s", (student_id,))
                created = cursor.fetchone()
    except psycopg.errors.UniqueViolation:
        raise ApiError(409, "Ce matricule existe déjà pour un autre étudiant.")
    except psycopg.Error:
        logger.exception("Création d'un étudiant UPB")
        raise ApiError(500, "Impossible de créer l'étudiant.")
    audit.annotate(target=str(student_id), target_type="etudiant", matricule=matricule)
    return respond(201, {"status": "success", "message": "Étudiant créé.", "data": created})


@router.post(
    "/api/admin_desinscrire",
    tags=["Administration"],
    summary="Désinscrire un étudiant du concours",
    description="Supprime l'inscription au concours (numéro de candidat) de l'étudiant. Le dossier étudiant et "
    "l'historique de pointage sont conservés. `404` si l'étudiant n'est pas inscrit. Le site d'inscription peut "
    "ensuite l'inscrire de nouveau (`/api/partenaire/inscrire`).",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(400, 401, 404, 500, 503)},
    openapi_extra=d.request_body(d.UnregisterBody, "Identifiant de l'étudiant."),
)
def unregister_student(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    student_id = positive_int((json_object(body) or {}).get("id"))
    if not student_id:
        raise ApiError(400, "Identifiant d'étudiant invalide.")
    audit.annotate(target_type="etudiant", target=str(student_id))
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM candidat_concours WHERE etudiant_id = %s RETURNING numero_candidat", (student_id,)
                )
                removed = cursor.fetchall()
                if not removed:
                    raise ApiError(404, "Cet étudiant n'est pas inscrit au concours.")
    except psycopg.Error:
        logger.exception("Désinscription UPB")
        raise ApiError(500, "Impossible de désinscrire l'étudiant.")
    audit.annotate(numero_candidat=removed[0]["numero_candidat"])
    return respond(200, {"status": "success", "message": "Étudiant désinscrit du concours."})


@router.post(
    "/api/admin_supprimer_etudiant",
    tags=["Administration"],
    summary="Supprimer définitivement un étudiant",
    description="Supprime le dossier de l'étudiant **et** ce qui en dépend : inscription au concours, historique de "
    "pointage et demandes de correction. Les demandes d'ajout de dossier déjà traitées sont conservées (sans lien). "
    "Irréversible. L'événement est tracé dans le journal (matricule et volumes, jamais les données personnelles).",
    response_model=None,
    responses={200: {"model": d.SuccessResponse}, **d.errors(400, 401, 404, 500, 503)},
    openapi_extra=d.request_body(d.UnregisterBody, "Identifiant de l'étudiant."),
)
def delete_student(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    student_id = positive_int((json_object(body) or {}).get("id"))
    if not student_id:
        raise ApiError(400, "Identifiant d'étudiant invalide.")
    audit.annotate(target_type="etudiant", target=str(student_id))
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute("SELECT matricule FROM etudiant WHERE id = %s FOR UPDATE", (student_id,))
                student = cursor.fetchone()
                if not student:
                    raise ApiError(404, "Étudiant introuvable.")
                cursor.execute("SELECT count(*) AS n FROM pointage_journalier WHERE etudiant_id = %s", (student_id,))
                pointages = cursor.fetchone()["n"]
                cursor.execute("SELECT numero_candidat FROM candidat_concours WHERE etudiant_id = %s", (student_id,))
                candidate = cursor.fetchone()
                cursor.execute("DELETE FROM etudiant WHERE id = %s", (student_id,))
    except psycopg.Error:
        logger.exception("Suppression d'un étudiant UPB")
        raise ApiError(500, "Impossible de supprimer l'étudiant.")
    audit.annotate(
        matricule=student["matricule"],
        etait_inscrit=bool(candidate),
        numero_candidat=candidate["numero_candidat"] if candidate else None,
        pointages_supprimes=pointages,
    )
    return respond(200, {"status": "success", "message": "Étudiant supprimé définitivement."})
