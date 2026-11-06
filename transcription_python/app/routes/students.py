import logging

import psycopg
from flask import Blueprint, jsonify, request

from ..auth import admin_required
from ..db import get_db
from ..student_helpers import (
    byte_length,
    parse_student_date,
    valid_email,
)
from .common import json_object, valid_positive_int

logger = logging.getLogger(__name__)
students = Blueprint("students", __name__)

EDITABLE_FIELDS = {
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


@students.route("/verifier.php", methods=["POST", "OPTIONS"])
def verify_student():
    if request.method == "OPTIONS":
        return "", 200
    data = json_object()
    matricule = data.get("matricule", "") if data else ""
    matricule = matricule.strip().upper() if isinstance(matricule, str) else ""
    if not matricule or len(matricule) > 40:
        return jsonify(status="error", message="Matricule requis."), 400

    query = """
        SELECT e.matricule, e.nom, e.prenoms, e.date_naissance,
               e.telephone, e.email, e.sexe, e.allergies_affection,
               f.libelle AS filiere, n.libelle AS niveau
        FROM etudiant e
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        WHERE e.matricule = %s
    """
    try:
        with get_db().cursor() as cursor:
            cursor.execute(query, (matricule,))
            row = cursor.fetchone()
    except psycopg.Error:
        logger.exception("Vérification étudiant UPB")
        return jsonify(
            status="error",
            message="Erreur lors de la vérification des informations.",
        ), 500

    if not row:
        return jsonify(
            status="error",
            message="Matricule et date de naissance non reconnus dans la base UPB.",
        ), 404
    return jsonify(
        status="success",
        message="Vos informations ont été retrouvées.",
        data=row,
    )


@students.route("/inscrire.php", methods=["POST", "OPTIONS"])
def enroll_student():
    if request.method == "OPTIONS":
        return "", 200
    data = json_object()
    matricule = data.get("matricule", "") if data else ""
    matricule = matricule.strip().upper() if isinstance(matricule, str) else ""
    if not matricule or len(matricule) > 40:
        return jsonify(status="error", message="Matricule requis."), 400

    connection = get_db()
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM etudiant WHERE matricule = %s",
                    (matricule,),
                )
                student = cursor.fetchone()
                if not student:
                    return jsonify(
                        status="error", message="Matricule non reconnu."
                    ), 404

                cursor.execute(
                    """
                    SELECT numero_candidat FROM candidat_concours
                    WHERE etudiant_id = %s LIMIT 1
                    """,
                    (student["id"],),
                )
                existing = cursor.fetchone()
                if existing:
                    return jsonify(
                        status="error",
                        message="Cet étudiant est déjà inscrit au concours Génie UPB.",
                        numero_candidat=existing["numero_candidat"],
                    ), 409

                import secrets

                numero_candidat = "GENIE-2026-" + secrets.token_hex(3)[:5].upper()
                cursor.execute(
                    """
                    INSERT INTO candidat_concours
                        (etudiant_id, numero_candidat, statut)
                    VALUES (%s, %s, 'EN_ATTENTE')
                    """,
                    (student["id"], numero_candidat),
                )
    except psycopg.Error:
        logger.exception("Inscription au concours UPB")
        return jsonify(
            status="error",
            message="Erreur lors de l'enregistrement de l'inscription.",
        ), 500

    return jsonify(
        status="success",
        message="Inscription au concours Génie UPB réussie avec succès !",
        data={
            "matricule": matricule,
            "numero_candidat": numero_candidat,
            "statut": "EN_ATTENTE",
        },
    ), 201


@students.route("/demander_modification.php", methods=["POST", "OPTIONS"])
def request_modification():
    if request.method == "OPTIONS":
        return "", 200
    data = json_object()
    if not data or not isinstance(data.get("matricule"), str) or not data["matricule"].strip():
        return jsonify(status="error", message="Matricule requis."), 400

    changes = {}
    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            return jsonify(status="error", message=f"Valeur invalide pour {field}."), 422
        value = value.strip()

        if field in {"nom", "prenoms"}:
            if not value or byte_length(value) > 150:
                return jsonify(
                    status="error",
                    message="Le nom et les prénoms sont obligatoires (150 caractères maximum).",
                ), 422
            changes[field] = value
        elif field == "date_naissance":
            parsed = parse_student_date(value)
            if not parsed:
                return jsonify(status="error", message="Date de naissance invalide."), 422
            changes[field] = parsed
        elif field in {"filiere", "niveau"}:
            if byte_length(value) > 150:
                return jsonify(status="error", message=f"Le champ {field} est trop long."), 422
            changes[field] = value or None
        elif field == "email":
            if not valid_email(value):
                return jsonify(status="error", message="Adresse e-mail invalide."), 422
            changes[field] = value or None
        elif field == "sexe":
            value = value.upper()
            if value and value not in {"M", "F"}:
                return jsonify(status="error", message="Le sexe doit être M ou F."), 422
            changes[field] = value or None
        else:
            max_length = 2000 if field == "allergies_affection" else 40
            if byte_length(value) > max_length:
                return jsonify(status="error", message=f"Le champ {field} est trop long."), 422
            changes[field] = value or None

    if not changes:
        return jsonify(status="error", message="Aucune modification proposée."), 422

    connection = get_db()
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
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
                    return jsonify(status="error", message="Matricule non reconnu."), 404

                different = {
                    field: value
                    for field, value in changes.items()
                    if str(student.get(field) or "") != str(value or "")
                }
                if not different:
                    return jsonify(status="error", message="Aucune différence à soumettre."), 422

                cursor.execute(
                    """
                    INSERT INTO etudiant_modification (etudiant_id, modifications)
                    VALUES (%s, %s::jsonb)
                    """,
                    (student["id"], json.dumps(different, ensure_ascii=False)),
                )
    except psycopg.errors.UniqueViolation:
        return jsonify(
            status="error",
            message="Une demande est déjà en attente pour ce dossier.",
        ), 409
    except psycopg.Error:
        logger.exception("Demande de modification UPB")
        return jsonify(
            status="error",
            message="Erreur lors de l’enregistrement de la demande.",
        ), 500

    return jsonify(
        status="success",
        message=(
            "Votre demande a été transmise. Les changements seront appliqués "
            "après validation par un administrateur."
        ),
    ), 201


@students.route("/update_etudiant.php", methods=["POST", "OPTIONS"])
@admin_required
def update_student():
    if request.method == "OPTIONS":
        return "", 200
    return _update_student_impl()


def _update_student_impl():
    data = json_object()
    if not data:
        return jsonify(status="error", message="Corps JSON invalide."), 400
    student_id = data.get("id")
    if not valid_positive_int(student_id):
        return jsonify(status="error", message="Données incomplètes."), 400

    editable = {
        "telephone": "text",
        "email": "email",
        "sexe": "sexe",
        "allergies_affection": "allergies",
    }
    assignments = []
    parameters = []
    for field, field_type in editable.items():
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            return jsonify(status="error", message=f"Valeur invalide pour {field}."), 422
        value = value.strip()
        if field_type == "email" and not valid_email(value):
            return jsonify(status="error", message="Adresse e-mail invalide."), 422
        if field_type == "sexe":
            value = value.upper()
            if value and value not in {"M", "F"}:
                return jsonify(status="error", message="Sexe invalide (M ou F)."), 422
        if field_type == "allergies" and byte_length(value) > 2000:
            return jsonify(
                status="error",
                message="Le champ allergies/affections est trop long.",
            ), 422
        assignments.append(f"{field} = %s")
        parameters.append(value or None)

    new_matricule = None
    if "date_naissance" in data and data["date_naissance"] != "":
        if not isinstance(data["date_naissance"], str):
            return jsonify(status="error", message="Date de naissance invalide."), 422
        birth_date = parse_student_date(data["date_naissance"].strip())
        if not birth_date:
            return jsonify(status="error", message="Date de naissance invalide."), 422

        try:
            from ..student_helpers import student_matricule

            connection = get_db()
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT matricule, nom, prenoms, date_naissance FROM etudiant WHERE id = %s",
                        (student_id,),
                    )
                    student = cursor.fetchone()
                    if not student:
                        return jsonify(status="error", message="Étudiant introuvable."), 404
                    new_matricule = student_matricule(
                        student["nom"], student["prenoms"], birth_date
                    )
                    if new_matricule != student["matricule"]:
                        cursor.execute(
                            "SELECT id FROM etudiant WHERE matricule = %s AND id <> %s",
                            (new_matricule, student_id),
                        )
                        if cursor.fetchone():
                            return jsonify(
                                status="error",
                                message="Le matricule calculé existe déjà.",
                            ), 409
                        assignments.append("matricule = %s")
                        parameters.append(new_matricule)
                    if birth_date != str(student["date_naissance"]):
                        assignments.append("date_naissance = %s")
                        parameters.append(birth_date)
        except ValueError as error:
            return jsonify(status="error", message=str(error)), 422
        except psycopg.Error:
            logger.exception("Lecture étudiant avant mise à jour UPB")
            return jsonify(
                status="error",
                message="Erreur lors de la lecture du dossier étudiant.",
            ), 500

    if not assignments:
        return jsonify(status="error", message="Données incomplètes."), 400

    parameters.append(student_id)
    try:
        connection = get_db()
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE etudiant SET {', '.join(assignments)} WHERE id = %s",
                    parameters,
                )
                if cursor.rowcount == 0:
                    return jsonify(status="error", message="Étudiant introuvable."), 404
    except psycopg.Error:
        logger.exception("Mise à jour étudiant UPB")
        return jsonify(status="error", message="Erreur de base de données."), 500

    return jsonify(
        status="success",
        message="Fiche étudiant mise à jour avec succès !",
        data={"matricule": new_matricule} if new_matricule else {},
    )


@students.route("/update_telephone.php", methods=["POST", "OPTIONS"])
@admin_required
def update_telephone():
    if request.method == "OPTIONS":
        return "", 200
    data = json_object()
    student_id = data.get("id") if data else None
    telephone = data.get("telephone") if data else None
    if not valid_positive_int(student_id) or not isinstance(telephone, str):
        return jsonify(status="error", message="Données incomplètes."), 400
    try:
        connection = get_db()
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE etudiant SET telephone = %s WHERE id = %s",
                    (telephone, student_id),
                )
                if cursor.rowcount == 0:
                    return jsonify(status="error", message="Étudiant introuvable."), 404
    except psycopg.Error:
        logger.exception("Mise à jour du téléphone UPB")
        return jsonify(status="error", message="Erreur de base de données."), 500
    return jsonify(
        status="success",
        message="Téléphone mis à jour avec succès !",
    )
