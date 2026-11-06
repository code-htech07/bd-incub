import logging
import re
from datetime import date

import psycopg
from flask import Blueprint, jsonify, request

from ..auth import admin_required
from ..db import get_db
from .common import json_object

logger = logging.getLogger(__name__)
attendance = Blueprint("attendance", __name__)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date(value):
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == value else None


@attendance.route("/pointage.php", methods=["GET", "POST", "OPTIONS"])
@admin_required
def manage_attendance():
    if request.method == "OPTIONS":
        return "", 200
    if request.method == "GET":
        return get_attendance()
    if request.method == "POST":
        return record_attendance()
    return jsonify(status="error", message="Méthode non autorisée."), 405


def get_attendance():
    requested_date = request.args.get("date", date.today().isoformat())
    parsed_date = parse_iso_date(requested_date)
    if not parsed_date:
        return jsonify(
            status="error",
            message="Date invalide. Format attendu : AAAA-MM-JJ.",
        ), 400
    day_label = request.args.get("jour")

    query = """
        SELECT p.id, e.id AS etudiant_id, e.matricule, e.nom, e.prenoms,
               f.libelle AS filiere, n.libelle AS niveau,
               c.numero_candidat, p.jour_libelle, p.heure_pointage,
               p.statut, p.qr_code_valide, p.created_at
        FROM pointage_journalier p
        JOIN etudiant e ON p.etudiant_id = e.id
        LEFT JOIN filiere f ON e.filiere_id = f.id
        LEFT JOIN niveau n ON e.niveau_id = n.id
        LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
        WHERE p.date_pointage = %s
        ORDER BY p.heure_pointage ASC
    """
    try:
        with get_db().cursor() as cursor:
            cursor.execute(query, (parsed_date,))
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) AS total FROM candidat_concours")
            total_registered = cursor.fetchone()["total"]
    except psycopg.Error:
        logger.exception("Lecture du pointage UPB")
        return jsonify(
            status="error",
            message="Erreur base de données lors de la lecture du pointage.",
        ), 500

    total_present = len(rows)
    rate = (
        round(total_present / total_registered * 100, 2)
        if total_registered > 0
        else 0
    )
    return jsonify(
        status="success",
        date=parsed_date,
        jour=day_label,
        stats={
            "total_presents": total_present,
            "total_inscrits": total_registered,
            "taux_presence": f"{rate} %",
        },
        data=rows,
    )


def record_attendance():
    data = json_object()
    if not data or not isinstance(data.get("matricule"), str) or not data["matricule"].strip():
        return jsonify(
            status="error",
            message="Le champ 'matricule' est requis.",
        ), 400

    matricule = data["matricule"].strip()
    requested_date = data.get("date", date.today().isoformat())
    parsed_date = parse_iso_date(requested_date)
    if not parsed_date:
        return jsonify(
            status="error",
            message="Date invalide. Format attendu : AAAA-MM-JJ.",
        ), 400
    day_label = data.get("jour_libelle")
    if day_label is not None and not isinstance(day_label, str):
        return jsonify(
            status="error",
            message="Valeur invalide pour jour_libelle.",
        ), 422

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
                        status="error",
                        message="Matricule introuvable dans la base UPB.",
                    ), 404

                cursor.execute(
                    """
                    SELECT id, statut
                    FROM pointage_journalier
                    WHERE etudiant_id = %s AND date_pointage = %s
                    """,
                    (student["id"], parsed_date),
                )
                existing = cursor.fetchone()
                if existing:
                    return jsonify(
                        status="error",
                        message=(
                            "Double pointage détecté : cet étudiant a déjà été "
                            f"pointé le {parsed_date}."
                        ),
                        data={"statut_existant": existing["statut"]},
                    ), 409

                cursor.execute(
                    """
                    INSERT INTO pointage_journalier
                        (etudiant_id, date_pointage, jour_libelle,
                         heure_pointage, statut, qr_code_valide, created_at)
                    VALUES (%s, %s, %s, NOW(), 'PRESENT', TRUE, NOW())
                    RETURNING id, heure_pointage
                    """,
                    (student["id"], parsed_date, day_label),
                )
                pointage = cursor.fetchone()
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == "uq_etudiant_date":
            return jsonify(
                status="error",
                message=(
                    "Double pointage détecté : cet étudiant a déjà été pointé ce jour."
                ),
            ), 409
        logger.exception("Contrainte unique durant le pointage UPB")
        return jsonify(
            status="error",
            message="Erreur base de données lors de l'enregistrement du pointage.",
        ), 500
    except psycopg.Error:
        logger.exception("Enregistrement du pointage UPB")
        return jsonify(
            status="error",
            message="Erreur base de données lors de l'enregistrement du pointage.",
        ), 500

    return jsonify(
        status="success",
        message=f"Présence enregistrée avec succès pour le matricule {matricule}.",
        data={
            "id": pointage["id"],
            "etudiant_id": student["id"],
            "matricule": matricule,
            "date_pointage": parsed_date,
            "statut": "PRESENT",
            "heure_pointage": pointage["heure_pointage"],
        },
    ), 201
