"""Règles de validité des dossiers étudiants : noms et matricule.

Noms : seuls l'alphabet français (A-Z et lettres accentuées du français), les espaces, les tirets
et les apostrophes sont acceptés. Les écritures non latines (arabe, chinois, cyrillique…), les
chiffres, les symboles et les emojis sont refusés. Le filtre porte sur les CARACTÈRES, jamais sur
l'origine ou la nationalité d'un nom.

Matricule : il n'est PAS calculé par le système ; il est fourni (saisi par l'étudiant ou présent
dans le fichier d'import) et doit respecter le format
    3 ou 4 lettres majuscules (nom et initiale du prénom) + 6 chiffres (date jjmmaa) + 4 chiffres
exemple KOUA1703980001.
"""
import calendar
import re

from .student_helpers import byte_length, normalize_student_name

FRENCH_LETTERS = "A-Za-zÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆàâäçéèêëîïôöùûüÿœæ"
_ALLOWED = re.compile(rf"[{FRENCH_LETTERS}' \-]")
_LETTER = re.compile(rf"[{FRENCH_LETTERS}]")
NAME_RULE = (
    "seules les lettres de l'alphabet français, les espaces, les tirets et les apostrophes "
    "sont acceptés"
)

MATRICULE_PATTERN = re.compile(r"([A-Z]{3,4})(\d{2})(\d{2})(\d{2})(\d{4})")
MATRICULE_RULE = (
    "Matricule invalide : 3 ou 4 lettres majuscules (nom et initiale du prénom), puis 6 chiffres "
    "(date de naissance jjmmaa), puis 4 chiffres. Exemple : KOUA1703980001."
)


def validate_name(value, label: str) -> str:
    """Retourne le nom nettoyé (espaces réduits, apostrophe typographique normalisée) ou lève ValueError."""
    if value is None:
        raise ValueError(f"{label} obligatoire.")
    if not isinstance(value, str):
        raise ValueError(f"{label} invalide.")
    cleaned = " ".join(value.replace("’", "'").replace("‘", "'").split())
    if not cleaned:
        raise ValueError(f"{label} obligatoire.")
    refused: list[str] = []
    for char in cleaned:
        if not _ALLOWED.fullmatch(char) and char not in refused:
            refused.append(char)
    if refused:
        shown = ", ".join(refused[:5])
        raise ValueError(f"{label} invalide : {NAME_RULE} (caractères refusés : {shown}).")
    if len(_LETTER.findall(cleaned)) < 2:
        raise ValueError(f"{label} invalide : au moins 2 lettres sont nécessaires.")
    if not _LETTER.fullmatch(cleaned[0]) or not _LETTER.fullmatch(cleaned[-1]):
        raise ValueError(f"{label} invalide : doit commencer et finir par une lettre.")
    if byte_length(cleaned) > 150:
        raise ValueError(f"{label} trop long (150 caractères maximum).")
    return cleaned


def validate_matricule(value) -> str:
    """Retourne le matricule normalisé (espaces autour retirés, majuscules) ou lève ValueError.

    Contrôle le format ET que les 6 chiffres centraux forment un jour/mois possibles (jjmm) ;
    la cohérence avec le nom ou la date de naissance saisis n'est pas imposée."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Matricule obligatoire.")
    cleaned = value.strip().upper()
    match = MATRICULE_PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError(MATRICULE_RULE)
    day, month = int(match.group(2)), int(match.group(3))
    if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(2000, month)[1]:
        raise ValueError(MATRICULE_RULE + " La date contenue dans le matricule est impossible.")
    return cleaned


# ---------------------------------------------------------------------------- doublons à l'import
# Un étudiant importé SANS matricule ne peut être identifié que par son nom : on refuse alors un homonyme
# exact (accents, casse, espaces ignorés) dont la date de naissance est identique ou inconnue d'un côté.
# Les lignes AVEC matricule ne sont contrôlées que sur l'unicité du matricule (les homonymes y sont permis).
def _name_key(nom: str, prenoms: str):
    try:
        return normalize_student_name(nom), normalize_student_name(prenoms)
    except ValueError:
        return None


def load_name_index(cursor) -> dict:
    """{(nom, prénoms normalisés): [(matricule ou None, date iso ou None), …]} pour tous les étudiants."""
    cursor.execute("SELECT matricule, nom, prenoms, date_naissance FROM etudiant")
    index: dict = {}
    for row in cursor.fetchall():
        key = _name_key(row["nom"], row["prenoms"])
        if key:
            index.setdefault(key, []).append(
                (row["matricule"], str(row["date_naissance"]) if row["date_naissance"] else None)
            )
    return index


def index_add(index: dict, nom: str, prenoms: str, birth_iso: str | None, matricule: str | None) -> None:
    key = _name_key(nom, prenoms)
    if key:
        index.setdefault(key, []).append((matricule, birth_iso))


def index_find(index: dict, nom: str, prenoms: str, birth_iso: str | None):
    """(matricule ou None) du premier homonyme incompatible, ou False s'il n'y en a pas."""
    key = _name_key(nom, prenoms)
    for matricule, existing in index.get(key, []) if key else []:
        if birth_iso is None or existing is None or existing == birth_iso:
            return matricule
    return False


# ---------------------------------------------------------------------------- filières et niveaux
# Listes fermées : une seule source de vérité (aussi publiée par GET /api/referentiels pour les pages).
FILIERES = ("ASSRI", "RIT", "MIAGE", "SEG", "SEA", "SJAP", "3EA")
NIVEAUX = ("Licence 1", "Licence 2", "Licence 3")


def _reference(value, allowed: tuple[str, ...], label: str, feminine: bool) -> str | None:
    """Forme canonique d'une valeur de la liste (casse et espaces ignorés) ; None si vide."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} invalide.")
    key = "".join(value.split()).casefold()
    if not key:
        return None
    for item in allowed:
        if "".join(item.split()).casefold() == key:
            return item
    shown = " ".join(value.split())[:40]
    unknown = f"{label} non reconnue" if feminine else f"{label} non reconnu"
    raise ValueError(f"{unknown} : « {shown} ». Valeurs autorisées : {', '.join(allowed)}.")


def validate_filiere(value) -> str | None:
    return _reference(value, FILIERES, "Filière", True)


def validate_niveau(value) -> str | None:
    return _reference(value, NIVEAUX, "Niveau", False)


REFERENCE_VALIDATORS = {"filiere": validate_filiere, "niveau": validate_niveau}
