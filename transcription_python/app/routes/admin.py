import csv
import io
import logging
import re
import unicodedata
from math import ceil

import psycopg
from flask import Blueprint, current_app, jsonify, request

from ..auth import admin_required
from ..db import get_db
from ..student_helpers import (
    fix_encoding,
    lookup_reference_id,
    parse_student_date,
    student_matricule,
)
from .common import json_object, valid_positive_int

logger = logging.getLogger(__name__)
admin = Blueprint("admin", __name__)


@admin.route("/admin_auth_check.php", methods=["POST", "OPTIONS"])
@admin_required
def check_admin_auth():
    if request.method == "OPTIONS":
        return "", 200
    return jsonify(
        status="success",
        message="Accès administrateur autorisé.",
    )


@admin.route("/get_etudiants.php", methods=["GET", "OPTIONS"])
@admin_required
def get_students():
    if request.method == "OPTIONS":
        return "", 200

    def query_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    page = max(1, query_int(request.args.get("page"), 1))
    per_page = min(
        current_app.config["MAX_PER_PAGE"],
        max(
            1,
            query_int(
                request.args.get("per_page"),
                current_app.config["DEFAULT_PER_PAGE"],
            ),
        ),
    )
    offset = (page - 1) * per_page
    search = request.args.get("q", "").strip()
    base = """
        FROM etudiant e
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
    """
    where = ""
    parameters = ()
    if search:
        where = """
            WHERE e.matricule ILIKE %s
               OR e.nom ILIKE %s
               OR e.prenoms ILIKE %s
               OR f.libelle ILIKE %s
        """
        pattern = f"%{search}%"
        parameters = (pattern, pattern, pattern, pattern)

    connection = get_db()
    try:
        with connection.cursor() as cursor:
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
                (*parameters, per_page, offset),
            )
            students = cursor.fetchall()

            stats = {
                "total": 0,
                "inscrits": 0,
                "telephones": 0,
                "emails": 0,
                "filieres": [],
                "niveaux": [],
            }
            cursor.execute("SELECT COUNT(*) AS total FROM etudiant")
            stats["total"] = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(DISTINCT etudiant_id) AS total FROM candidat_concours"
            )
            stats["inscrits"] = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COUNT(*) AS total FROM etudiant
                WHERE telephone IS NOT NULL AND TRIM(telephone) <> ''
                """
            )
            stats["telephones"] = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COUNT(*) AS total FROM etudiant
                WHERE email IS NOT NULL AND TRIM(email) <> ''
                """
            )
            stats["emails"] = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COALESCE(f.libelle, 'Non renseignée') AS libelle,
                       COUNT(e.id) AS total
                FROM etudiant e
                LEFT JOIN filiere f ON e.filiere_id = f.id
                GROUP BY COALESCE(f.libelle, 'Non renseignée')
                ORDER BY total DESC
                """
            )
            stats["filieres"] = cursor.fetchall()
            cursor.execute(
                """
                SELECT COALESCE(n.libelle, 'Non renseigné') AS libelle,
                       COUNT(e.id) AS total
                FROM etudiant e
                LEFT JOIN niveau n ON e.niveau_id = n.id
                GROUP BY COALESCE(n.libelle, 'Non renseigné')
                ORDER BY total DESC
                """
            )
            stats["niveaux"] = cursor.fetchall()
    except psycopg.Error:
        logger.exception("Lecture des étudiants UPB")
        return jsonify(
            status="error",
            message="Impossible de charger les étudiants.",
        ), 500

    for student in students:
        for field, value in student.items():
            student[field] = fix_encoding(value)
    for key in ("filieres", "niveaux"):
        for row in stats[key]:
            row["libelle"] = fix_encoding(row["libelle"])

    return jsonify(
        status="success",
        data=students,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=ceil(total / per_page),
        stats=stats,
    )


@admin.route("/admin_modifications.php", methods=["GET", "POST", "OPTIONS"])
@admin_required
def manage_modifications():
    if request.method == "OPTIONS":
        return "", 200
    connection = get_db()

    if request.method == "GET":
        try:
            with connection.cursor() as cursor:
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
            return jsonify(
                status="error",
                message="Impossible de charger les demandes.",
            ), 500
        return jsonify(status="success", data=requests)

    data = json_object()
    if not data:
        return jsonify(status="error", message="Demande ou action invalide."), 400
    request_id = data.get("id")
    action = data.get("action", "")
    if not valid_positive_int(request_id) or action not in {"approve", "reject"}:
        return jsonify(status="error", message="Demande ou action invalide."), 400

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
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
                    return jsonify(
                        status="error",
                        message="Demande déjà traitée ou introuvable.",
                    ), 404

                if action == "reject":
                    cursor.execute(
                        """
                        UPDATE etudiant_modification
                        SET statut = 'REFUSEE', reviewed_at = NOW()
                        WHERE id = %s
                        """,
                        (request_id,),
                    )
                    return jsonify(status="success", message="Demande refusée.")

                changes = modification["modifications"]
                allowed = {
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
                if (
                    not isinstance(changes, dict)
                    or not changes
                    or set(changes) - allowed
                ):
                    raise ValueError("Contenu de demande invalide.")

                cursor.execute(
                    """
                    SELECT matricule, nom, prenoms, date_naissance
                    FROM etudiant WHERE id = %s FOR UPDATE
                    """,
                    (modification["etudiant_id"],),
                )
                student = cursor.fetchone()
                if not student:
                    raise ValueError("Étudiant associé à la demande introuvable.")

                matricule = student["matricule"]
                if {"nom", "prenoms", "date_naissance"} & changes.keys():
                    nom = changes.get("nom", student["nom"])
                    prenoms = changes.get("prenoms", student["prenoms"])
                    birth_date = changes.get(
                        "date_naissance", student["date_naissance"]
                    )
                    if not birth_date:
                        raise ValueError(
                            "Une date de naissance est requise pour recalculer le matricule."
                        )
                    matricule = student_matricule(nom, prenoms, birth_date)
                    cursor.execute(
                        "SELECT id FROM etudiant WHERE matricule = %s AND id <> %s",
                        (matricule, modification["etudiant_id"]),
                    )
                    if cursor.fetchone():
                        raise ValueError(
                            "Le matricule calculé existe déjà pour un autre étudiant."
                        )
                    changes["matricule"] = matricule

                if "filiere" in changes:
                    filiere = changes.pop("filiere")
                    changes["filiere_id"] = (
                        None
                        if filiere is None
                        else lookup_reference_id("filiere", str(filiere))
                    )
                if "niveau" in changes:
                    niveau = changes.pop("niveau")
                    changes["niveau_id"] = (
                        None
                        if niveau is None
                        else lookup_reference_id("niveau", str(niveau))
                    )

                assignments = []
                values = []
                for field, value in changes.items():
                    if field == "date_naissance" and value is not None:
                        value = parse_student_date(str(value))
                        if not value:
                            raise ValueError("Date de naissance invalide.")
                    if field in {
                        "telephone",
                        "email",
                        "sexe",
                        "allergies_affection",
                    } and value == "":
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
    except ValueError as error:
        return jsonify(status="error", message=str(error)), 422
    except psycopg.Error:
        logger.exception("Traitement de demande UPB")
        return jsonify(
            status="error",
            message="Erreur lors du traitement de la demande.",
        ), 500

    return jsonify(
        status="success",
        message="Demande approuvée et informations mises à jour.",
        matricule=matricule,
    )


@admin.route("/import_etudiants.php", methods=["POST", "OPTIONS"])
@admin_required
def import_students():
    if request.method == "OPTIONS":
        return "", 200
    upload = request.files.get("fichier")
    if upload is None:
        return jsonify(
            status="error",
            message="Sélectionnez un fichier CSV valide.",
        ), 400
    if not upload.filename or not upload.filename.lower().endswith(".csv"):
        return jsonify(
            status="error",
            message="Le fichier doit être un CSV de 5 Mo maximum.",
        ), 422

    content = upload.stream.read(current_app.config["CSV_MAX_BYTES"] + 1)
    if len(content) > current_app.config["CSV_MAX_BYTES"]:
        return jsonify(
            status="error",
            message="Le fichier doit être un CSV de 5 Mo maximum.",
        ), 422
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(
            status="error",
            message="Le fichier CSV doit être encodé en UTF-8.",
        ), 422
    if not text:
        return jsonify(status="error", message="Le fichier CSV est vide."), 422

    try:
        semicolon_header = next(csv.reader(io.StringIO(text), delimiter=";"), [])
        comma_header = next(csv.reader(io.StringIO(text), delimiter=","), [])
        delimiter = ";" if len(semicolon_header) >= len(comma_header) else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        headers = next(reader, [])
    except csv.Error:
        return jsonify(status="error", message="Fichier CSV invalide."), 422

    headers = [
        re.sub(
            r"[^a-z0-9]+",
            "_",
            unicodedata.normalize("NFKD", str(value).strip())
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower(),
        ).strip("_")
        for value in headers
    ]
    aliases = {
        "prenom": "prenoms",
        "date_de_naissance": "date_naissance",
        "date_naiss": "date_naissance",
        "e_mail": "email",
        "allergies": "allergies_affection",
        "allergie_affection": "allergies_affection",
        "allergies_affections": "allergies_affection",
    }
    headers = [aliases.get(header, header) for header in headers]
    required = {"nom", "prenoms", "date_naissance", "filiere", "niveau"}
    if not required.issubset(headers):
        return jsonify(
            status="error",
            message=(
                "Colonnes obligatoires : nom, prenoms, date_naissance, "
                "filiere, niveau."
            ),
        ), 422

    errors = []
    seen_matricules = set()
    reference_cache = {"filiere": {}, "niveau": {}}
    imported = 0
    connection = get_db()
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                for row_number, values in enumerate(reader, start=2):
                    if not values or not any(value.strip() for value in values):
                        continue
                    if row_number > 5001:
                        errors.append("Le fichier dépasse la limite de 5 000 lignes.")
                        break
                    row = {
                        key: (values[index].strip() if index < len(values) else "")
                        for index, key in enumerate(headers)
                    }
                    nom = row.get("nom", "")
                    prenoms = row.get("prenoms", "")
                    birth_date = parse_student_date(row.get("date_naissance", ""))
                    filiere = row.get("filiere", "")
                    niveau = row.get("niveau", "")
                    email = row.get("email", "")
                    sexe = row.get("sexe", "").upper()
                    telephone = row.get("telephone", "")
                    allergies = row.get("allergies_affection", "")

                    if not nom or not prenoms or not birth_date or not filiere or not niveau:
                        errors.append(
                            f"Ligne {row_number} : nom, prénoms, date de naissance, "
                            "filière et niveau sont obligatoires."
                        )
                        continue
                    if not valid_email(email):
                        errors.append(
                            f"Ligne {row_number} : adresse e-mail invalide."
                        )
                        continue
                    if sexe and sexe not in {"M", "F"}:
                        errors.append(f"Ligne {row_number} : le sexe doit être M ou F.")
                        continue
                    try:
                        matricule = student_matricule(nom, prenoms, birth_date)
                    except ValueError as error:
                        errors.append(f"Ligne {row_number} : {error}")
                        continue

                    cursor.execute(
                        "SELECT id FROM etudiant WHERE matricule = %s",
                        (matricule,),
                    )
                    if cursor.fetchone() or matricule in seen_matricules:
                        errors.append(
                            f"Ligne {row_number} : matricule déjà présent ou dupliqué "
                            f"dans le CSV ({matricule})."
                        )
                        continue

                    reference_ids = {}
                    for table, label in (("filiere", filiere), ("niveau", niveau)):
                        key = label.strip().lower()
                        if key not in reference_cache[table]:
                            reference_cache[table][key] = lookup_reference_id(
                                table, label
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
                    seen_matricules.add(matricule)
                    imported += 1

                if errors:
                    return jsonify(
                        status="error",
                        message=(
                            "Aucune ligne importée : corrigez les erreurs puis réessayez."
                        ),
                        errors=errors[:50],
                    ), 422
                if imported == 0:
                    return jsonify(
                        status="error",
                        message="Le fichier ne contient aucun étudiant à importer.",
                    ), 422
    except psycopg.Error:
        logger.exception("Import CSV UPB")
        return jsonify(
            status="error",
            message=(
                "Erreur lors de l'import. Vérifiez le format CSV et le schéma de la base."
            ),
        ), 500

    return jsonify(
        status="success",
        message=f"{imported} étudiant(s) importé(s) avec succès.",
        imported=imported,
        matricules=sorted(seen_matricules),
    ), 201
