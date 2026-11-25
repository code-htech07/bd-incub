"""Listes fermées : filières (ASSRI, RIT, MIAGE, SEG, SEA, SJAP, 3EA) et niveaux (Licence 1, 2, 3).

Données de test : noms « Zzt… », matricules « ZZT… », filière historique « ZZTEST Ancienne » (nettoyés).
"""
import re
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_db_flow import client, import_csv, student  # noqa: F401

from app import config
from app.main import create_app
from app.student_rules import FILIERES, NIVEAUX, validate_filiere, validate_niveau

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
FILIERE_LIST = ["ASSRI", "RIT", "MIAGE", "SEG", "SEA", "SJAP", "3EA"]
NIVEAU_LIST = ["Licence 1", "Licence 2", "Licence 3"]


# ------------------------------------------------------------------ règles (sans base)
def test_lists_are_exactly_the_requested_values():
    assert list(FILIERES) == FILIERE_LIST and list(NIVEAUX) == NIVEAU_LIST


@pytest.mark.parametrize("value,expected", [
    ("ASSRI", "ASSRI"), ("assri", "ASSRI"), (" Miage ", "MIAGE"), ("3ea", "3EA"), ("s j a p", "SJAP"), ("sEg", "SEG"),
    ("", None), ("   ", None), (None, None),
])
def test_filiere_accepts_list_values_in_any_case(value, expected):
    assert validate_filiere(value) == expected


@pytest.mark.parametrize("value", ["Génie Civil", "ASRI", "RIT2", "Informatique", "L1", "3 EA X", 5])
def test_filiere_refuses_other_values(value):
    with pytest.raises(ValueError) as error:
        validate_filiere(value)
    assert "ASSRI, RIT, MIAGE, SEG, SEA, SJAP, 3EA" in str(error.value) or "invalide" in str(error.value)


@pytest.mark.parametrize("value,expected", [
    ("Licence 1", "Licence 1"), ("licence 2", "Licence 2"), ("LICENCE3", "Licence 3"), (" licence  3 ", "Licence 3"), ("", None), (None, None),
])
def test_niveau_accepts_list_values(value, expected):
    assert validate_niveau(value) == expected


@pytest.mark.parametrize("value", ["Master 1", "L1", "Licence 4", "Licence", "Doctorat"])
def test_niveau_refuses_other_values(value):
    with pytest.raises(ValueError) as error:
        validate_niveau(value)
    assert "Licence 1, Licence 2, Licence 3" in str(error.value)


# ------------------------------------------------------------------ pages, composant et modèle
def test_public_referentiels_route(client):
    anonymous = TestClient(create_app())
    with anonymous:
        response = anonymous.get("/api/referentiels")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "filieres": FILIERE_LIST, "niveaux": NIVEAU_LIST}
    assert response.headers["cache-control"] == "no-store"


def test_pages_use_the_custom_dropdown_with_the_same_lists(client):
    for name in ("index.html", "admin.html"):
        page = (FRONTEND / name).read_text(encoding="utf-8")
        assert '<script src="upb-select.js"></script>' in page, name
        match = re.search(r"const REFERENTIELS = \{ filieres: (\[.*?\]), niveaux: (\[.*?\]) \};", page)
        assert match, name
        import json
        assert json.loads(match.group(1)) == FILIERE_LIST and json.loads(match.group(2)) == NIVEAU_LIST, name
        assert "/api/referentiels" in page, name
    portal = (FRONTEND / "index.html").read_text(encoding="utf-8")
    admin = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    for select_id in ("filiere", "niveau"):
        assert re.search(rf'<select id="{select_id}" data-upb-select', portal), select_id
    for select_id in ("s-filiere", "s-niveau"):
        assert re.search(rf'<select id="{select_id}" data-upb-select', admin), select_id
    assert 'id="s-filieres"' not in admin and "<datalist" not in admin  # plus de saisie libre
    assert '<input type="text" id="filiere">' not in portal and '<input type="text" id="niveau">' not in portal
    assert client.get("/upb-select.js").status_code == 200


def test_custom_dropdown_is_accessible_and_keeps_native_semantics():
    code = (FRONTEND / "upb-select.js").read_text(encoding="utf-8")
    for needle in ("role', 'combobox'", "role', 'listbox'", "role', 'option'", "aria-expanded", "aria-selected",
                   "ArrowDown", "Escape", "Home", "hors liste", "dispatchEvent(new Event('change'"):
        assert needle in code, needle


def test_import_template_uses_valid_list_values():
    lines = (FRONTEND / "modele_import_etudiants.csv").read_text(encoding="utf-8-sig").splitlines()
    header = lines[0].split(";")
    examples = [ln[2:].split(";") for ln in lines if ln.startswith("# ") and ln.count(";") == 9]
    assert examples
    for cells in examples:
        row = dict(zip(header, cells))
        assert validate_filiere(row["filiere"]) == (row["filiere"] or None)
        assert validate_niveau(row["niveau"]) == (row["niveau"] or None)
    assert "ASSRI, RIT, MIAGE, SEG, SEA, SJAP ou 3EA" in "\n".join(lines)


# ------------------------------------------------------------------ import CSV
def test_import_normalises_and_validates_filiere_and_niveau(client):
    csv = "nom;prenoms;filiere;niveau\nZztest;Alice;assri;licence 2\nZztest;Bob;;\nZztest;Carl;3EA;LICENCE3\n"
    response = import_csv(client, csv)
    assert response.status_code == 201, response.text
    rows = {r["prenoms"]: r for r in client.get("/api/get_etudiants", params={"q": "Zztest", "per_page": 50}).json()["data"]}
    assert (rows["Alice"]["filiere"], rows["Alice"]["niveau"]) == ("ASSRI", "Licence 2")
    assert (rows["Bob"]["filiere"], rows["Bob"]["niveau"]) == (None, None)
    assert (rows["Carl"]["filiere"], rows["Carl"]["niveau"]) == ("3EA", "Licence 3")


def test_import_rejects_unknown_filiere_and_niveau_all_or_nothing(client):
    csv = ("nom;prenoms;filiere;niveau\nZztest;Alice;RIT;Licence 1\nZztest;Bob;Génie Civil;Licence 1\n"
           "Zztest;Carl;RIT;Master 1\nZztest;Dina;ASRI;L2\n")
    response = import_csv(client, csv)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert len(errors) == 3
    assert errors[0].startswith("Ligne 3") and "Filière non reconnue" in errors[0] and "ASSRI, RIT, MIAGE" in errors[0]
    assert errors[1].startswith("Ligne 4") and "Niveau non reconnu" in errors[1] and "Licence 1, Licence 2, Licence 3" in errors[1]
    assert errors[2].startswith("Ligne 5") and "Filière non reconnue" in errors[2]
    assert client.get("/api/get_etudiants", params={"q": "Zztest"}).json()["total"] == 0


# ------------------------------------------------------------------ formulaires candidat et administration
def legacy_student():
    """Étudiant historique dont la filière (« ZZTEST Ancienne ») n'est pas dans la liste."""
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        fid = conn.execute("INSERT INTO filiere (libelle) VALUES ('ZZTEST Ancienne') RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO etudiant (matricule, nom, prenoms, filiere_id) VALUES ('ZZTL1703980001', 'Zztlegacy', 'Alice', %s)",
            (fid,),
        )
        conn.commit()
    return "ZZTL1703980001"


def test_candidate_creation_request_uses_the_lists(client):
    base = {"matricule": "ZZTC0506000001", "nom": "Zztcandidat", "prenoms": "Carole"}
    for field, bad in (("filiere", "Génie Civil"), ("niveau", "Master 1")):
        response = client.post("/api/demander_creation", json={**base, field: bad})
        assert response.status_code == 422, field
        assert "non reconnu" in response.json()["message"] and "Valeurs autorisées" in response.json()["message"]
    assert client.post("/api/demander_creation", json={**base, "filiere": "miage", "niveau": "licence 3"}).status_code == 201
    pending = [r for r in client.get("/api/admin_creations").json()["data"] if r["matricule_saisi"] == "ZZTC0506000001"][0]
    assert pending["donnees"]["filiere"] == "MIAGE" and pending["donnees"]["niveau"] == "Licence 3"  # forme canonique
    assert client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"}).status_code == 200
    created = student(client, "ZZTC0506000001")
    assert (created["filiere"], created["niveau"]) == ("MIAGE", "Licence 3")


def test_candidate_correction_uses_the_lists_but_tolerates_an_unchanged_legacy_value(client):
    matricule = legacy_student()
    url = "/api/demander_modification"
    # Valeur hors liste renvoyée telle quelle par le formulaire : ce n'est pas une modification, elle est tolérée
    assert client.post(url, json={"matricule": matricule, "filiere": "ZZTEST Ancienne", "email": "a@exemple.ci"}).status_code == 201
    request = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == matricule][0]
    assert request["modifications"] == {"email": "a@exemple.ci"}  # la filière inchangée n'est pas proposée
    assert client.post("/api/admin_modifications", json={"id": request["id"], "action": "reject"}).status_code == 200
    # Changer pour une autre valeur hors liste : refusé ; pour une valeur de la liste : accepté et normalisé
    response = client.post(url, json={"matricule": matricule, "filiere": "Génie Mécanique"})
    assert response.status_code == 422 and "Filière non reconnue" in response.json()["message"]
    response = client.post(url, json={"matricule": matricule, "niveau": "Master 1"})
    assert response.status_code == 422 and "Niveau non reconnu" in response.json()["message"]
    assert client.post(url, json={"matricule": matricule, "filiere": "rit", "niveau": "licence 1"}).status_code == 201
    request = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == matricule][0]
    assert request["modifications"] == {"filiere": "RIT", "niveau": "Licence 1"}
    assert client.post("/api/admin_modifications", json={"id": request["id"], "action": "approve"}).status_code == 200
    data = student(client, matricule)
    assert (data["filiere"], data["niveau"]) == ("RIT", "Licence 1")


def test_admin_forms_use_the_lists_and_keep_legacy_values(client):
    # Création
    for field, bad in (("filiere", "Génie Civil"), ("niveau", "Master 2")):
        response = client.post("/api/admin_etudiants", json={"nom": "Zztest", "prenoms": "Alice", field: bad})
        assert response.status_code == 422 and "Valeurs autorisées" in response.json()["message"], field
    created = client.post("/api/admin_etudiants", json={"nom": "Zztest", "prenoms": "Alice", "matricule": "ZZTA1703980001", "filiere": "sea", "niveau": "licence 3"})
    assert created.status_code == 201, created.text
    assert (created.json()["data"]["filiere"], created.json()["data"]["niveau"]) == ("SEA", "Licence 3")
    # Modification
    row = created.json()["data"]
    update = lambda **fields: client.post("/api/update_etudiant", json={"id": row["id"], **fields})  # noqa: E731
    assert update(filiere="Génie Civil").status_code == 422 and update(niveau="Master 1").status_code == 422
    assert update(filiere="sjap", niveau="Licence 1").status_code == 200
    assert (student(client, "ZZTA1703980001")["filiere"], student(client, "ZZTA1703980001")["niveau"]) == ("SJAP", "Licence 1")
    assert update(filiere="", niveau="").status_code == 200  # effacer reste possible
    assert student(client, "ZZTA1703980001")["filiere"] is None
    # Étudiant historique : sa valeur hors liste, renvoyée par le formulaire, n'est ni refusée ni effacée
    matricule = legacy_student()
    legacy = [r for r in client.get("/api/get_etudiants", params={"q": "Zztlegacy"}).json()["data"]][0]
    assert legacy["filiere"] == "ZZTEST Ancienne"
    ok = client.post("/api/update_etudiant", json={"id": legacy["id"], "filiere": "ZZTEST Ancienne", "telephone": "0102030405"})
    assert ok.status_code == 200, ok.text
    data = student(client, matricule)
    assert data["filiere"] == "ZZTEST Ancienne" and data["telephone"] == "0102030405"
    assert client.post("/api/update_etudiant", json={"id": legacy["id"], "filiere": "Autre ancienne"}).status_code == 422
