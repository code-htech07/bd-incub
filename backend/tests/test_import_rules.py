"""Import et saisie des dossiers.

Règles testées :
  * à l'import, SEULS nom et prénoms sont obligatoires (matricule compris) ;
  * le matricule n'est jamais calculé : fourni, il est importé tel quel (format + unicité contrôlés) ;
    absent, l'étudiant est créé sans matricule, à attribuer ensuite ;
  * noms : alphabet français uniquement.

Données de test : noms commençant par « Zzt », matricules « ZZT… » (nettoyés par la fixture `client`).
"""
from pathlib import Path

import pytest
from test_db_flow import client, import_csv, student, student_id  # noqa: F401

from app.student_rules import validate_matricule, validate_name

TEMPLATE = Path(__file__).resolve().parents[2] / "frontend" / "modele_import_etudiants.csv"
OPTIONAL_COLUMNS = (
    "matricule", "date_naissance", "filiere", "niveau", "telephone", "email", "sexe", "allergies_affection",
)


def listing(client, q="Zzt"):
    return client.get("/api/get_etudiants", params={"q": q, "per_page": 100}).json()


# ------------------------------------------------------------------ règles unitaires (sans base)
@pytest.mark.parametrize("name", [
    "Kouadio", "N'Guessan", "N’Guessan", "Marie-Claire", "Zoé", "François", "Aya Mélanie", "KONÉ", "Œuvre",
    "  Ouattara   Awa ",
])
def test_french_alphabet_names_are_accepted(name):
    assert validate_name(name, "Nom") == " ".join(name.replace("’", "'").split())


@pytest.mark.parametrize("name,refused", [
    ("阿里", "阿"), ("Мария", "М"), ("محمد", "م"), ("Jean2", "2"), ("Ali@", "@"), ("John_Doe", "_"),
    ("Jos.", "."), ("ñandú", "ñ"), ("Łukasz", "Ł"), ("Müller ß", "ß"), ("😀ab", "😀"), ("12", "1"),
])
def test_other_alphabets_digits_and_symbols_are_refused(name, refused):
    with pytest.raises(ValueError) as error:
        validate_name(name, "Nom")
    assert "alphabet français" in str(error.value) and refused in str(error.value)


@pytest.mark.parametrize("name", ["", "   ", "A", "-Alain", "Alain-", "'Alain", "x" * 200])
def test_incomplete_names_are_refused(name):
    with pytest.raises(ValueError):
        validate_name(name, "Prénoms")


@pytest.mark.parametrize("value,expected", [
    ("KOUA1703980001", "KOUA1703980001"), (" koua1703980001 ", "KOUA1703980001"), ("YOK1703980001", "YOK1703980001"),
    ("KOUA0102000002", "KOUA0102000002"), ("KOUA2902000001", "KOUA2902000001"),
])
def test_valid_matricule_formats(value, expected):
    assert validate_matricule(value) == expected


@pytest.mark.parametrize("value", [
    "", "   ", None, 5, "KOUA170398001", "KOUA17039800011", "KO1703980001", "KOUAB1703980001", "KOUA 1703980001",
    "KOUA170398000A", "ÉKOU1703980001", "KOUA-1703980001", "1703980001KOUA", "KOUA3113980001", "KOUA1713980001",
    "KOUA0000000001", "KOUA3002000001",
])
def test_invalid_matricule_formats(value):
    with pytest.raises(ValueError) as error:
        validate_matricule(value)
    assert "Matricule" in str(error.value)


# ------------------------------------------------------------------ import : tout est facultatif sauf nom et prénoms
def test_only_nom_and_prenoms_are_required(client):
    response = import_csv(client, "nom;prenoms\nZztest;Alice\nZzténé;Zoé\n")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["imported"] == 2 and body["sans_matricule"] == 2 and body["matricules"] == []
    assert "sans matricule" in body["message"]
    rows = listing(client)["data"]
    assert sorted(r["nom"] for r in rows) == ["Zztest", "Zzténé"]
    assert all(r["matricule"] is None and r["date_naissance"] is None and r["filiere"] is None
               and r["niveau"] is None and r["telephone"] is None and r["email"] is None and r["sexe"] is None
               for r in rows)


@pytest.mark.parametrize("column", OPTIONAL_COLUMNS)
def test_each_optional_column_can_be_absent_empty_or_filled(client, column):
    """Chaque colonne facultative : absente du fichier, présente mais vide, ou renseignée."""
    values = {
        "matricule": "ZZTO1703980001", "date_naissance": "17/03/1998", "filiere": "RIT",
        "niveau": "Licence 1", "telephone": "0102030405", "email": "o@exemple.ci", "sexe": "F",
        "allergies_affection": "Arachides",
    }
    assert import_csv(client, "nom;prenoms\nZztest;Absente\n").status_code == 201
    assert import_csv(client, f"nom;prenoms;{column}\nZztest;Videe;\n").status_code == 201
    response = import_csv(client, f"nom;prenoms;{column}\nZztest;Remplie;{values[column]}\n")
    assert response.status_code == 201, (column, response.text)
    assert listing(client)["total"] == 3


def test_everything_can_be_empty_except_nom_and_prenoms(client):
    header = "nom;prenoms;" + ";".join(OPTIONAL_COLUMNS)
    response = import_csv(client, header + "\nZztest;Alice" + ";" * len(OPTIONAL_COLUMNS) + "\n")
    assert response.status_code == 201, response.text
    assert response.json()["imported"] == 1 and response.json()["sans_matricule"] == 1


@pytest.mark.parametrize("header", ["nom;matricule", "prenoms;matricule", "matricule;date_naissance;filiere", "filiere;niveau"])
def test_nom_and_prenoms_columns_are_mandatory(client, header):
    cells = ";".join(["Zztest"] * (header.count(";") + 1))
    response = import_csv(client, header + "\n" + cells + "\n")
    assert response.status_code == 422 and response.json()["message"] == "Colonnes obligatoires : nom, prenoms."


def test_nom_and_prenoms_values_are_mandatory_on_each_row(client):
    response = import_csv(client, "nom;prenoms;matricule\n;Alice;ZZTA1703980001\nZztest;;ZZTB1703980001\n ; ;\n")
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert len(errors) == 2 and all("le nom et les prénoms sont obligatoires" in e for e in errors)
    assert listing(client)["total"] == 0


# ------------------------------------------------------------------ import : matricule
def test_matricule_is_imported_as_is_never_computed(client):
    # Le matricule fourni n'a aucun lien imposé avec le nom : il est repris tel quel (en majuscules)
    response = import_csv(client, "matricule;nom;prenoms;date_naissance\nzzta1703980001;Zztest;Alice;17/03/1998\nZZTQ0101000007;Zztest;Bob;\n")
    assert response.status_code == 201, response.text
    assert response.json()["matricules"] == ["ZZTA1703980001", "ZZTQ0101000007"]
    assert response.json()["sans_matricule"] == 0
    assert student(client, "ZZTQ0101000007")["prenoms"] == "Bob"
    assert student(client, "ZZTA1703980001")["date_naissance"] == "1998-03-17"


@pytest.mark.parametrize("bad", ["KOUA170398", "KOUA 1703980001", "ZZTA17039800AB", "ZZTA3113980001", "ZZTA0000000001", "ZZTAB1703980001"])
def test_invalid_matricule_format_is_rejected_with_its_line(client, bad):
    response = import_csv(client, f"nom;prenoms;matricule\nZztest;Alice;ZZTA1703980001\nZztest;Bob;{bad}\n")
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error.startswith("Ligne 3 : Matricule invalide") and "KOUA1703980001" in error
    assert listing(client)["total"] == 0  # tout ou rien : la ligne 2, valide, n'est pas importée non plus


def test_duplicate_matricules_are_rejected(client):
    assert import_csv(client, "nom;prenoms;matricule\nZztest;Alice;ZZTA1703980001\n").status_code == 201
    # Déjà en base (casse ignorée) mais attribué à une autre personne (nom sans rapport), puis doublon dans le fichier
    response = import_csv(client, "nom;prenoms;matricule\nZzxwvuts;Autre;zzta1703980001\n")
    assert response.status_code == 422 and "appartient à un autre étudiant" in response.json()["errors"][0]
    response = import_csv(client, "nom;prenoms;matricule\nZztest;Bob;ZZTB0205990001\nZztest;Bobby;ZZTB0205990001\n")
    assert response.status_code == 422 and response.json()["errors"][0].startswith("Ligne 3")
    assert listing(client)["total"] == 1


def test_homonyms_with_different_matricules_are_allowed(client):
    response = import_csv(client, "nom;prenoms;matricule\nZztest;Alice;ZZTA1703980001\nZztest;Alice;ZZTA0205990001\n")
    assert response.status_code == 201, response.text
    assert listing(client)["total"] == 2


def test_rows_without_matricule_are_matched_by_name(client):
    assert import_csv(client, "nom;prenoms;matricule\nZztest;Alice;ZZTA1703980001\nZztest;Bob;\n").status_code == 201
    # Même nom et prénoms (accents, casse, espaces ignorés), sans matricule : c'est le même étudiant, rien de créé
    for row in ("ZZTEST;alice", " Zztest ;  Alice ", "Zztest;Bob"):
        response = import_csv(client, "nom;prenoms\n" + row + "\n")
        assert response.status_code == 201, row
        assert response.json()["imported"] == 0 and response.json()["unchanged"] == 1, row
    assert listing(client)["total"] == 2
    # Répété dans le même fichier (sans matricule) : erreur
    response = import_csv(client, "nom;prenoms\nZztest;Dina\nZztest;Dina\n")
    assert response.status_code == 422 and response.json()["errors"][0].startswith("Ligne 3")
    # Dates connues et différentes : ce sont deux personnes
    response = import_csv(client, "nom;prenoms;date_naissance\nZztest;Eric;01/01/1990\nZztest;Eric;02/02/1991\n")
    assert response.status_code == 201, response.text


def test_a_matricule_can_be_assigned_later_and_changed_by_the_admin(client):
    assert import_csv(client, "nom;prenoms\nZztest;Alice\n").status_code == 201
    row = listing(client)["data"][0]
    assert row["matricule"] is None
    # Sans matricule : l'étudiant est introuvable par le portail
    assert client.post("/api/verifier", json={"matricule": "ZZTA1703980001"}).status_code == 404
    update = lambda matricule: client.post("/api/update_etudiant", json={"id": row["id"], "matricule": matricule})  # noqa: E731
    assert update("pas-un-matricule").status_code == 422
    response = update("zzta1703980001")
    assert response.status_code == 200 and response.json()["data"] == {"matricule": "ZZTA1703980001"}
    assert student(client, "ZZTA1703980001")["nom"] == "Zztest"
    assert update("ZZTA1703980001").status_code == 200  # même valeur : sans effet
    response = update("ZZTB0205990001")  # un matricule déjà attribué peut être modifié par l'administrateur
    assert response.status_code == 200 and response.json()["data"] == {"matricule": "ZZTB0205990001"}
    assert client.post("/api/verifier", json={"matricule": "ZZTA1703980001"}).status_code == 404
    assert student(client, "ZZTB0205990001")["nom"] == "Zztest"
    assert update("ZZTA1703980001").status_code == 200  # et être rétabli
    # Un matricule déjà pris par un autre étudiant : 409
    assert import_csv(client, "nom;prenoms\nZztest;Bob\n").status_code == 201
    bob = [r for r in listing(client)["data"] if r["prenoms"] == "Bob"][0]
    response = client.post("/api/update_etudiant", json={"id": bob["id"], "matricule": "ZZTA1703980001"})
    assert response.status_code == 409 and "existe déjà" in response.json()["message"]


# ------------------------------------------------------------------ import : noms, dates
def test_foreign_alphabets_digits_and_symbols_are_rejected_all_or_nothing(client):
    rows = [
        "Zztest;Valide", "阿里;Zzt", "Zztest;Мария", "Zztest2;Alice", "Zztest;Jo@n", "Zztest;😀ab",
        "Zzt;A", ";Alice", "Zztest;",
    ]
    response = import_csv(client, "nom;prenoms\n" + "\n".join(rows) + "\n")
    assert response.status_code == 422
    body = response.json()
    assert body["message"].startswith("Aucune ligne importée")
    assert "Ligne 3" in body["errors"][0] and "阿" in body["errors"][0] and "alphabet français" in body["errors"][0]
    assert any("Ligne 5" in e and "2" in e for e in body["errors"])
    assert any("Ligne 6" in e and "@" in e for e in body["errors"])
    assert any("Ligne 7" in e and "😀" in e for e in body["errors"])
    assert any("Ligne 8" in e and "au moins 2 lettres" in e for e in body["errors"])
    assert any("Ligne 9" in e and "obligatoires" in e for e in body["errors"])
    assert any("Ligne 10" in e and "obligatoires" in e for e in body["errors"])
    assert listing(client)["total"] == 0  # la ligne valide n'a pas été importée


def test_invalid_birth_date_is_an_error_but_missing_is_not(client):
    response = import_csv(client, "nom;prenoms;date_naissance\nZztest;Alice;31/02/1998\n")
    assert response.status_code == 422 and "date de naissance invalide" in response.json()["errors"][0]
    response = import_csv(client, "nom;prenoms;date_naissance\nZztest;Alice;17/03/1998\nZztest;Bob;\n")
    assert response.status_code == 201


# ------------------------------------------------------------------ modèle d'import
def test_import_template_matches_the_importer(client):
    raw = TEMPLATE.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw  # UTF-8 avec BOM, fins de ligne Windows (Excel)
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == "nom;prenoms;matricule;date_naissance;filiere;niveau;telephone;email;sexe;allergies_affection"
    # Tel quel : seules des lignes d'aide, rien n'est importé et aucune erreur n'est signalée
    response = client.post("/api/import_etudiants", files={"fichier": ("modele.csv", raw)})
    assert response.status_code == 422
    assert response.json()["message"] == "Le fichier ne contient aucun étudiant à importer."
    assert "errors" not in response.json()
    # Les exemples (# retiré) s'importent sans erreur. Pour le nettoyage des tests : noms préfixés « Zzt »
    # et matricules préfixés « ZZT ».
    examples = [ln[2:].split(";") for ln in lines if ln.startswith("# ") and ln.count(";") == 9]
    assert len(examples) == 3
    cells = [["Zzt" + c[0].title(), c[1], ("ZZT" + c[2][3:]) if c[2] else "", *c[3:]] for c in examples]
    response = import_csv(client, lines[0] + "\n" + "\n".join(";".join(c) for c in cells) + "\n")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["imported"] == 3 and body["sans_matricule"] == 1  # le 3e exemple n'a pas de matricule
    assert body["matricules"] == ["ZZTA1703980001", "ZZTA2005000001"]
    alain = student(client, "ZZTA1703980001")
    assert alain["prenoms"] == "Alain" and alain["filiere"] == "ASSRI" and alain["niveau"] == "Licence 3"
    assert student(client, "ZZTA2005000001")["date_naissance"] is None  # la date reste facultative


# ------------------------------------------------------------------ saisies des candidats et approbations
def test_candidate_requests_apply_the_same_rules(client):
    url_create, url_edit = "/api/demander_creation", "/api/demander_modification"
    base = {"matricule": "ZZTI0101000001", "nom": "Zztnodate", "prenoms": "Ibrahim"}
    # Le matricule est SAISI et doit respecter le format ; il n'est pas calculé
    for bad in ("ZZTSANSDATE01", "ZZTI000000001", "zzt i0101000001"):
        response = client.post(url_create, json={**base, "matricule": bad})
        assert response.status_code == 422 and response.json()["message"].startswith("Matricule invalide"), bad
    # Sans date de naissance : acceptée ; dossier créé à l'approbation avec le matricule saisi
    assert client.post(url_create, json={**base, "matricule": " zzti0101000001 "}).status_code == 201
    pending = [r for r in client.get("/api/admin_creations").json()["data"] if r["matricule_saisi"] == "ZZTI0101000001"][0]
    assert "date_naissance" not in pending["donnees"]
    approved = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["matricule"] == "ZZTI0101000001"
    created = student(client, "ZZTI0101000001")
    assert created["nom"] == "Zztnodate" and created["date_naissance"] is None
    # Matricule déjà pris : 409 ; un homonyme avec un autre matricule est une autre personne
    assert client.post(url_create, json=base).status_code == 409
    assert client.post(url_create, json={**base, "matricule": "ZZTI0202000002"}).status_code == 201
    # Alphabets étrangers refusés à la saisie
    for field, bad in (("nom", "阿里"), ("prenoms", "Мария"), ("nom", "Zzt3")):
        response = client.post(url_create, json={**base, "matricule": "ZZTI0303000003", field: bad})
        assert response.status_code == 422 and "alphabet français" in response.json()["message"], (field, bad)
        response = client.post(url_edit, json={"matricule": "ZZTI0101000001", field: bad})
        assert response.status_code == 422 and "alphabet français" in response.json()["message"], (field, bad)
    # Date vide = non renseignée (aucune erreur)
    response = client.post(url_edit, json={"matricule": "ZZTI0101000001", "date_naissance": "", "email": "ibra@exemple.ci"})
    assert response.status_code == 201, response.text


def test_corrections_never_change_the_matricule(client):
    assert import_csv(client, "nom;prenoms;matricule\nZztest;Alice;ZZTA1703980001\n").status_code == 201

    def approve(changes):
        assert client.post("/api/demander_modification", json={"matricule": "ZZTA1703980001", **changes}).status_code == 201
        request_id = [m for m in client.get("/api/admin_modifications").json()["data"] if m["matricule"] == "ZZTA1703980001"][0]["id"]
        response = client.post("/api/admin_modifications", json={"id": request_id, "action": "approve"})
        assert response.status_code == 200, response.text
        assert response.json()["matricule"] == "ZZTA1703980001"

    approve({"nom": "Zztesu"})
    approve({"prenoms": "Zoé"})  # même changement d'initiale : le matricule reste
    approve({"date_naissance": "1990-05-05"})  # une date différente de celle du matricule : acceptée
    final = student(client, "ZZTA1703980001")
    assert final["nom"] == "Zztesu" and final["prenoms"] == "Zoé" and final["date_naissance"] == "1990-05-05"
