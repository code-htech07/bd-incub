import re
import unicodedata
from datetime import date, datetime

from .db import get_db


def normalize_student_name(value):
    ascii_name = (
        unicodedata.normalize("NFKD", value.strip())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    letters = re.sub(r"[^A-Za-z]", "", ascii_name).upper()
    if not letters:
        raise ValueError("Nom ou prénom invalide.")
    return letters


def student_matricule(nom, prenoms, date_naissance):
    parsed = parse_student_date(date_naissance)
    if parsed is None:
        raise ValueError("Date de naissance invalide.")

    birth_date = date.fromisoformat(parsed)
    return (
        normalize_student_name(nom)[:3]
        + normalize_student_name(prenoms)[:1]
        + birth_date.strftime("%d%m%y")
        + "0001"
    )


def parse_student_date(value):
    if isinstance(value, (date, datetime)):
        parsed_date = value.date() if isinstance(value, datetime) else value
        return parsed_date.isoformat() if parsed_date <= date.today() else None

    value = value.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed_date = datetime.strptime(value, pattern).date()
        except ValueError:
            continue
        if parsed_date <= date.today() and parsed_date.strftime(pattern) == value:
            return parsed_date.isoformat()
    return None


def lookup_reference_id(table, label):
    if table not in {"filiere", "niveau"}:
        raise ValueError("Référence invalide.")
    if label is None or not label.strip():
        return None

    connection = get_db()
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id FROM {table} WHERE LOWER(libelle) = LOWER(%s) LIMIT 1",
            (label.strip(),),
        )
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute(
            f"INSERT INTO {table} (libelle) VALUES (%s) RETURNING id",
            (label.strip(),),
        )
        return cursor.fetchone()["id"]


def valid_email(value):
    if not value:
        return True
    return bool(
        re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value,
            flags=re.UNICODE,
        )
    )


def byte_length(value):
    return len(value.encode("utf-8"))


def fix_encoding(value):
    if not isinstance(value, str) or not re.search(r"Ã[\xA2-\xAF©]", value):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
