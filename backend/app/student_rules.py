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
from difflib import SequenceMatcher

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


def upper_text(value):
    """Texte libre (ex. allergies) en majuscules ; les valeurs non texte sont rendues telles quelles."""
    return value.upper() if isinstance(value, str) else value


def validate_name(value, label: str) -> str:
    """Nom nettoyé (espaces réduits, apostrophe normalisée) EN MAJUSCULES, ou lève ValueError."""
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
    return cleaned.upper()  # toujours en majuscules en base, quelle que soit la saisie


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
def name_key(nom: str, prenoms: str):
    try:
        return normalize_student_name(nom), normalize_student_name(prenoms)
    except ValueError:
        return None


def load_name_index(cursor) -> dict:
    """{(nom, prénoms normalisés): [(matricule ou None, date iso ou None), …]} pour tous les étudiants."""
    cursor.execute("SELECT matricule, nom, prenoms, date_naissance FROM etudiant")
    index: dict = {}
    for row in cursor.fetchall():
        key = name_key(row["nom"], row["prenoms"])
        if key:
            index.setdefault(key, []).append(
                (row["matricule"], str(row["date_naissance"]) if row["date_naissance"] else None)
            )
    return index


def index_add(index: dict, nom: str, prenoms: str, birth_iso: str | None, matricule: str | None) -> None:
    key = name_key(nom, prenoms)
    if key:
        index.setdefault(key, []).append((matricule, birth_iso))


def index_find(index: dict, nom: str, prenoms: str, birth_iso: str | None):
    """(matricule ou None) du premier homonyme incompatible, ou False s'il n'y en a pas."""
    key = name_key(nom, prenoms)
    for matricule, existing in index.get(key, []) if key else []:
        if birth_iso is None or existing is None or existing == birth_iso:
            return matricule
    return False


# ---------------------------------------------------------------------------- filières et niveaux
# Listes fermées : une seule source de vérité (aussi publiée par GET /api/referentiels pour les pages).
FILIERES = ("ASSRI", "RIT", "MIAGE", "SEG", "SEA", "SJAP", "3EA")
NIVEAUX = ("LICENCE 1", "LICENCE 2", "LICENCE 3")


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


# ---------------------------------------------------------------------------- comparaison des noms
# Les noms et prénoms sont déjà en base pour tous les étudiants (même sans autre donnée). Une demande est
# comparée à ces noms ; de légères fautes de frappe sont tolérées (score de ressemblance >= NAME_TOLERANCE).
NAME_TOLERANCE = 0.80


def _tokens(value: str) -> list[str]:
    out = []
    for part in re.split(r"[\s\-'’]+", value or ""):
        try:
            out.append(normalize_student_name(part))
        except ValueError:
            continue
    return out


def _token_similarity(a: str, b: str) -> float:
    """Ressemblance 0..1 de deux séries de mots ; un prénom en plus ou en moins n'est pas pénalisé."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    remaining = list(long_)
    total = 0.0
    for token in short:
        best = max(remaining, key=lambda other: SequenceMatcher(None, token, other).ratio())
        total += SequenceMatcher(None, token, best).ratio()
        remaining.remove(best)
    return total / len(short)


def name_similarity(nom_a: str, prenoms_a: str, nom_b: str, prenoms_b: str) -> float:
    """Le plus faible des scores nom / prénoms (les deux doivent se ressembler)."""
    return round(min(_token_similarity(nom_a, nom_b), _token_similarity(prenoms_a, prenoms_b)), 3)


def name_level(score: float) -> str:
    """« identique », « proche » (faute légère tolérée) ou « different » d'après le score seul."""
    return "identique" if score >= 0.999 else "proche" if score >= NAME_TOLERANCE else "different"


def name_comparison(nom_a: str, prenoms_a: str, nom_b: str, prenoms_b: str) -> tuple[float, str]:
    """(score, niveau) ; un prénom en trop ou en moins donne « partiel », jamais « identique ».

    « identique » : mêmes mots (ordre indifférent) ; « proche » : mêmes mots à une faute de frappe près ;
    « partiel » : une des deux versions a des prénoms ou des mots en plus (ex. « KOUASSI FRANCK » pour
    « KOUASSI OI AMANY FRANCK ABEL ») : la ressemblance ne prouve pas qu'il s'agit de la même personne ;
    « different » : ressemblance insuffisante."""
    score = name_similarity(nom_a, prenoms_a, nom_b, prenoms_b)
    if score < NAME_TOLERANCE:
        return score, "different"
    if len(_tokens(nom_a)) != len(_tokens(nom_b)) or len(_tokens(prenoms_a)) != len(_tokens(prenoms_b)):
        return score, "partiel"
    return score, "identique" if score >= 0.999 else "proche"


_LEVEL_RANK = {"identique": 3, "proche": 2, "partiel": 1}


def load_students_for_matching(cursor) -> list[dict]:
    cursor.execute("SELECT id, matricule, nom, prenoms, date_naissance FROM etudiant")
    return cursor.fetchall()


def best_name_match(rows: list[dict], nom: str, prenoms: str, birth_iso: str | None = None, without_matricule: bool = False):
    """(ligne, score, niveau) du dossier existant dont le nom ressemble le plus (>= tolérance), sinon None.

    Deux dates de naissance connues et différentes excluent le rapprochement. Les noms « identique » et « proche » passent
    avant les « partiel » ; à égalité, un dossier sans matricule est préféré. `without_matricule=True` (dossiers à compléter à
    l'approbation) ne considère que les dossiers sans matricule et n'accepte un nom « partiel » que si les deux dates de
    naissance sont connues et égales."""
    best = None
    for row in rows:
        if without_matricule and row["matricule"]:
            continue
        existing = str(row["date_naissance"]) if row["date_naissance"] else None
        if birth_iso and existing and birth_iso != existing:
            continue
        score, level = name_comparison(nom, prenoms, row["nom"], row["prenoms"])
        if level == "different":
            continue
        if without_matricule and level == "partiel" and not (birth_iso and existing and birth_iso == existing):
            continue
        rank = (_LEVEL_RANK[level], score, not row["matricule"])
        if best is None or rank > best[0]:
            best = (rank, row, score, level)
    return (best[1], best[2], best[3]) if best else None
