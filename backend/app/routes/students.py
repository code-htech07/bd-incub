"""Routes étudiants : vérification, inscription, demande de correction, mise à jour admin.
"""
import json
import logging
from typing import Any

import psycopg
from fastapi import APIRouter, Depends

from .. import audit
from ..auth import require_admin
from ..db import Database, get_db
from ..errors import ApiError, respond
from ..student_rules import validate_matricule, validate_name
from ..student_helpers import (
    byte_length,
    parse_student_date,
    valid_email,
)
from .. import docs as d
from .common import json_body, json_object, positive_int

logger = logging.getLogger(__name__)
router = APIRouter()

EDITABLE_FIELDS = (
    "nom",
    "prenoms",
    "date_naissance",
    "filiere",
    "niveau",
    "telephone",
    "email",
    "sexe",
    "allergies_affection",
)


def _clean_matricule(data: dict | None) -> str:
    value = data.get("matricule") if data else None
    value = value.strip().upper() if isinstance(value, str) else ""
    if not value or len(value) > 40:
        raise ApiError(400, "Matricule requis.")
    return value


@router.post(
    "/api/verifier",
    tags=["Portail étudiant"],
    summary="Vérifier l'identité d'un étudiant par son matricule",
    description=(
        "Retrouve le dossier d'un étudiant à partir de son matricule et renvoie son "
        "identité (nom, prénoms, date de naissance), ses coordonnées et sa filière. "
        "**Route publique, sans jeton.** Le serveur ne reçoit pas la date de naissance "
        "saisie par l'utilisateur : c'est à l'appelant de comparer, côté serveur, les "
        "informations renvoyées avec celles que l'utilisateur déclare. Voir le guide "
        "d'intégration (`docs/GUIDE_INTEGRATION.md`)."
    ),
    response_model=None,
    responses={
        200: {"model": d.VerifyResponse, "description": "Étudiant trouvé."},
        **d.errors(400, 404, 500),
    },
    openapi_extra=d.request_body(d.MatriculeBody, "Matricule à vérifier."),
)
def verify_student(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    matricule = _clean_matricule(json_object(body))
    audit.annotate(target_type="matricule", target=matricule)
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT e.matricule, e.nom, e.prenoms, e.date_naissance,
                       e.telephone, e.email, e.sexe, e.allergies_affection,
                       f.libelle AS filiere, n.libelle AS niveau
                FROM etudiant e
                LEFT JOIN filiere f ON e.filiere_id = f.id
                LEFT JOIN niveau n ON e.niveau_id = n.id
                WHERE e.matricule = %s
                """,
                (matricule,),
            )
            row = cursor.fetchone()
    except psycopg.Error:
        logger.exception("Vérification étudiant UPB")
        raise ApiError(500, "Erreur lors de la vérification des informations.")
    if not row:
        raise ApiError(404, "Matricule non reconnus dans la base I-UPB.")
    return respond(
        200,
        {"status": "success", "message": "Vos informations ont été retrouvées.", "data": row},
    )


def _validated_fields(data: dict) -> dict[str, Any]:
    """Valide et nettoie les champs de dossier présents dans `data`."""
    changes: dict[str, Any] = {}
    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            raise ApiError(422, f"Valeur invalide pour {field}.")
        value = value.strip()

        if field in ("nom", "prenoms"):
            if not value or byte_length(value) > 150:
                raise ApiError(
                    422, "Le nom et les prénoms sont obligatoires (150 caractères maximum)."
                )
            try:
                changes[field] = validate_name(value, "Nom" if field == "nom" else "Prénoms")
            except ValueError as error:
                raise ApiError(422, str(error))
        elif field == "date_naissance":
            if not value:
                continue  # date facultative : champ vide = non renseigné
            parsed = parse_student_date(value)
            if not parsed:
                raise ApiError(422, "Date de naissance invalide.")
            changes[field] = parsed
        elif field in ("filiere", "niveau"):
            if byte_length(value) > 150:
                raise ApiError(422, f"Le champ {field} est trop long.")
            changes[field] = value or None
        elif field == "email":
            if not valid_email(value):
                raise ApiError(422, "Adresse e-mail invalide.")
            changes[field] = value or None
        elif field == "sexe":
            value = value.upper()
            if value and value not in ("M", "F"):
                raise ApiError(422, "Le sexe doit être M ou F.")
            changes[field] = value or None
        else:
            max_length = 2000 if field == "allergies_affection" else 40
            if byte_length(value) > max_length:
                raise ApiError(422, f"Le champ {field} est trop long.")
            changes[field] = value or None
    return changes


@router.post(
    "/api/demander_modification",
    tags=["Portail étudiant"],
    summary="Proposer une correction de dossier",
    description="Enregistre une demande de correction qui sera appliquée après validation "
    "par un administrateur. Seuls les champs réellement différents sont retenus. "
    "Une seule demande en attente par dossier.",
    response_model=None,
    responses={
        201: {"model": d.SuccessResponse, "description": "Demande transmise."},
        **d.errors(400, 404, 409, 422, 500),
    },
    openapi_extra=d.request_body(d.ModificationBody, "Matricule et champs à corriger."),
)
def request_modification(
    body: Any = Depends(json_body), db: Database = Depends(get_db)
):
    data = json_object(body)
    if not data or not isinstance(data.get("matricule"), str) or not data["matricule"].strip():
        raise ApiError(400, "Matricule requis.")
    audit.annotate(target_type="matricule", target=data["matricule"].strip().upper())

    changes = _validated_fields(data)
    audit.annotate(champs=sorted(changes))
    if not changes:
        raise ApiError(422, "Aucune modification proposée.")

    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT e.id, e.nom, e.prenoms, e.date_naissance,
                           e.telephone, e.email, e.sexe, e.allergies_affection,
                           f.libelle AS filiere, n.libelle AS niveau
                    FROM etudiant e
                    LEFT JOIN filiere f ON e.filiere_id = f.id
                    LEFT JOIN niveau n ON e.niveau_id = n.id
                    WHERE e.matricule = %s
                    """,
                    (data["matricule"].strip().upper(),),
                )
                student = cursor.fetchone()
                if not student:
                    raise ApiError(404, "Matricule non reconnu.")

                different = {
                    field: value
                    for field, value in changes.items()
                    if str(student.get(field) or "") != str(value or "")
                }
                if not different:
                    raise ApiError(422, "Aucune différence à soumettre.")

                cursor.execute(
                    """
                    INSERT INTO etudiant_modification (etudiant_id, modifications)
                    VALUES (%s, %s::jsonb)
                    """,
                    (student["id"], json.dumps(different, ensure_ascii=False)),
                )
    except psycopg.errors.UniqueViolation:
        raise ApiError(409, "Une demande est déjà en attente pour ce dossier.")
    except psycopg.Error:
        logger.exception("Demande de modification UPB")
        raise ApiError(500, "Erreur lors de l’enregistrement de la demande.")
    return respond(
        201,
        {
            "status": "success",
            "message": "Votre demande a été transmise. Les changements seront "
            "appliqués après validation par un administrateur.",
        },
    )


@router.post(
    "/api/demander_creation",
    tags=["Portail étudiant"],
    summary="Soumettre son dossier quand le matricule est inconnu",
    description="Un candidat dont le matricule n'est pas en base renseigne ses informations. "
    "La demande est enregistrée **en attente** : le dossier n'est créé qu'après validation "
    "par un administrateur, avec **le matricule saisi par le candidat** (il n'est pas calculé : il doit "
    "respecter le format, ex. `KOUA1703980001`, et ne pas exister déjà). **Le matricule, le nom et les "
    "prénoms sont obligatoires**, la date de naissance est facultative ; nom et prénoms n'acceptent que "
    "les lettres de l'alphabet français, espaces, tirets et apostrophes.",
    response_model=None,
    responses={
        201: {"model": d.SuccessResponse, "description": "Demande transmise."},
        **d.errors(400, 409, 422, 500),
    },
    openapi_extra=d.request_body(d.CreationBody, "Matricule saisi et informations du candidat."),
)
def request_creation(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body)
    matricule = _clean_matricule(data)
    audit.annotate(target_type="matricule", target=matricule)
    try:
        matricule = validate_matricule(matricule)
    except ValueError as error:
        raise ApiError(422, str(error))
    fields = _validated_fields(data)
    if not all(fields.get(key) for key in ("nom", "prenoms")):
        raise ApiError(422, "Le nom et les prénoms sont obligatoires.")
    audit.annotate(champs=sorted(fields))

    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute("SELECT 1 FROM etudiant WHERE matricule = %s", (matricule,))
                if cursor.fetchone():
                    raise ApiError(
                        409,
                        "Un dossier existe déjà pour ce matricule : "
                        "utilisez la vérification de votre matricule.",
                    )
                cursor.execute(
                    """
                    INSERT INTO etudiant_creation_demande (matricule_saisi, donnees)
                    VALUES (%s, %s::jsonb)
                    """,
                    (matricule, json.dumps(fields, ensure_ascii=False)),
                )
    except psycopg.errors.UniqueViolation:
        raise ApiError(409, "Une demande est déjà en attente pour ce matricule.")
    except psycopg.Error:
        logger.exception("Demande de création de dossier UPB")
        raise ApiError(500, "Erreur lors de l’enregistrement de la demande.")
    return respond(
        201,
        {
            "status": "success",
            "message": "Votre dossier a été transmis. Il sera créé après validation "
            "par un administrateur : revenez vérifier votre matricule ensuite.",
        },
    )


@router.post(
    "/api/update_etudiant",
    dependencies=[Depends(require_admin)],
    tags=["Administration"],
    summary="Modifier directement une fiche étudiant",
    description="Modifie téléphone, e-mail, sexe, allergies/affections et/ou date de "
    "naissance (qui recalcule le matricule). Une chaîne vide efface le champ.",
    response_model=None,
    responses={
        200: {"description": "Fiche mise à jour ; `data.matricule` si le matricule a changé."},
        **d.errors(400, 401, 404, 409, 422, 500, 503),
    },
    openapi_extra=d.request_body(d.UpdateStudentBody, "Identifiant et champs à modifier."),
)
def update_student(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    data = json_object(body)
    if data is None:
        raise ApiError(400, "Corps JSON invalide.")
    student_id = positive_int(data.get("id"))
    if not student_id:
        raise ApiError(400, "Données incomplètes.")
    audit.annotate(target_type="etudiant", target=str(student_id))

    editable = {
        "telephone": "str",
        "email": "email",
        "sexe": "sexe",
        "allergies_affection": "allergies",
    }
    assignments: list[str] = []
    parameters: list[Any] = []
    for field, field_type in editable.items():
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            raise ApiError(422, f"Valeur invalide pour {field}.")
        value = value.strip()
        if value and field_type == "email" and not valid_email(value):
            raise ApiError(422, "Adresse e-mail invalide.")
        if field_type == "sexe" and value and value not in ("M", "F"):
            raise ApiError(422, "Sexe invalide (M ou F).")
        if field_type == "allergies" and byte_length(value) > 2000:
            raise ApiError(422, "Le champ allergies/affections est trop long.")
        assignments.append(f"{field} = %s")
        parameters.append(value or None)

    # Date de naissance et matricule : le matricule n'est JAMAIS recalculé. Il peut seulement être
    # attribué à un étudiant qui n'en a pas encore (import sans matricule) ; un matricule existant
    # est un identifiant et ne se modifie pas.
    new_matricule = None
    same_matricule = False
    raw_birth = data.get("date_naissance")
    raw_matricule = data.get("matricule")
    birth_date = None
    if raw_birth is not None and raw_birth != "":
        if not isinstance(raw_birth, str):
            raise ApiError(422, "Date de naissance invalide.")
        birth_date = parse_student_date(raw_birth)
        if not birth_date:
            raise ApiError(422, "Date de naissance invalide.")
    if raw_matricule is not None and raw_matricule != "":
        try:
            new_matricule = validate_matricule(raw_matricule)
        except ValueError as error:
            raise ApiError(422, str(error))
    if birth_date or new_matricule:
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT matricule, date_naissance FROM etudiant WHERE id = %s", (student_id,)
                )
                student = cursor.fetchone()
                if not student:
                    raise ApiError(404, "Étudiant introuvable.")
                if new_matricule:
                    if student["matricule"] and student["matricule"] != new_matricule:
                        raise ApiError(
                            422, "Le matricule est déjà attribué et ne peut pas être modifié."
                        )
                    if student["matricule"] == new_matricule:
                        new_matricule = None  # déjà le sien : rien à faire
                        same_matricule = True
                    else:
                        cursor.execute(
                            "SELECT id FROM etudiant WHERE matricule = %s AND id <> %s",
                            (new_matricule, student_id),
                        )
                        if cursor.fetchone():
                            raise ApiError(409, "Ce matricule existe déjà pour un autre étudiant.")
                        assignments.append("matricule = %s")
                        parameters.append(new_matricule)
                if birth_date and birth_date != str(student["date_naissance"]):
                    assignments.append("date_naissance = %s")
                    parameters.append(birth_date)
        except psycopg.Error:
            logger.exception("Lecture étudiant avant mise à jour UPB")
            raise ApiError(500, "Erreur lors de la lecture du dossier étudiant.")

    if not assignments:
        if same_matricule:  # le matricule demandé est déjà celui de l'étudiant : succès sans effet
            return respond(200, {"status": "success", "message": "Fiche inchangée.", "data": {}})
        raise ApiError(400, "Données incomplètes.")

    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    f"UPDATE etudiant SET {', '.join(assignments)} WHERE id = %s",
                    (*parameters, student_id),
                )
    except psycopg.Error:
        logger.exception("Mise à jour étudiant UPB")
        raise ApiError(500, "Erreur de base de données.")
    audit.annotate(
        champs=[a.split(" = ")[0] for a in assignments],
        matricule_attribue=bool(new_matricule),
    )
    return respond(
        200,
        {
            "status": "success",
            "message": "Fiche étudiant mise à jour avec succès !",
            "data": {"matricule": new_matricule} if new_matricule else {},
        },
    )


@router.post(
    "/api/update_telephone",
    dependencies=[Depends(require_admin)],
    tags=["Administration"],
    summary="Modifier le téléphone (ancienne route)",
    description="Conservée pour compatibilité ; préférer `/api/update_etudiant`.",
    response_model=None,
    deprecated=True,
    responses={200: {"model": d.SuccessResponse}, **d.errors(400, 401, 500, 503)},
    openapi_extra=d.request_body(d.UpdatePhoneBody, "Identifiant et nouveau téléphone."),
)
def update_telephone(body: Any = Depends(json_body), db: Database = Depends(get_db)):
    """Ancienne route : préférer /api/update_etudiant."""
    data = json_object(body) or {}
    student_id = positive_int(data.get("id"))
    telephone = data.get("telephone")
    if not student_id or not isinstance(telephone, str):
        raise ApiError(400, "Données incomplètes.")
    audit.annotate(target_type="etudiant", target=str(student_id), champs=["telephone"])
    try:
        with db.transaction():
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE etudiant SET telephone = %s WHERE id = %s", (telephone, student_id)
                )
    except psycopg.Error:
        logger.exception("Mise à jour du téléphone UPB")
        raise ApiError(500, "Erreur de base de données.")
    return respond(200, {"status": "success", "message": "Téléphone mis à jour avec succès !"})
