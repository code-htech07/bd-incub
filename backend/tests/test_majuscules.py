"""Tout est enregistré en MAJUSCULES (noms, prénoms, allergies, filière, niveau, matricule) et la casse ne compte jamais.

Données de test : noms « Zzt… », matricules « ZZT… » (nettoyés par la fixture `client`).
"""
import psycopg
import pytest
from test_db_flow import client, import_csv, student  # noqa: F401

from app import config
from app.student_rules import upper_text, validate_name


def listing(client, q="Zzt"):
    return client.get("/api/get_etudiants", params={"q": q, "per_page": 100}).json()


# ------------------------------------------------------------------ règles (sans base)
@pytest.mark.parametrize("value,expected", [
    ("Kouamé", "KOUAMÉ"), ("n'guessan", "N'GUESSAN"), ("jean-éric  marie", "JEAN-ÉRIC MARIE"), ("ÉLODIE", "ÉLODIE"),
    ("œuvre", "ŒUVRE"), ("Ça", "ÇA"),
])
def test_names_are_uppercased(value, expected):
    assert validate_name(value, "Nom") == expected


def test_upper_text_only_touches_strings():
    assert upper_text("arachides, pénicilline") == "ARACHIDES, PÉNICILLINE"
    assert upper_text(None) is None and upper_text(5) == 5


# ------------------------------------------------------------------ import
def test_import_stores_everything_in_uppercase(client):
    csv = ("nom;prenoms;matricule;filiere;niveau;allergies_affection;email;sexe\n"
           "zztest;alice marie;zzta1703980001;assri;licence 3;arachides et pénicilline;Alice@Exemple.ci;f\n")
    assert import_csv(client, csv).status_code == 201
    data = student(client, "zzta1703980001")  # recherche sans tenir compte de la casse
    assert (data["nom"], data["prenoms"], data["matricule"]) == ("ZZTEST", "ALICE MARIE", "ZZTA1703980001")
    assert (data["filiere"], data["niveau"], data["allergies_affection"], data["sexe"]) == ("ASSRI", "LICENCE 3", "ARACHIDES ET PÉNICILLINE", "F")
    assert data["email"] == "Alice@Exemple.ci"  # l'adresse e-mail n'est pas modifiée


def test_reimport_with_other_case_is_the_same_student(client):
    assert import_csv(client, "nom;prenoms;matricule\nZztest;Alice;ZZTA1703980001\n").status_code == 201
    for row in ("zztest;alice;zzta1703980001", "ZZTEST;ALICE;ZZTA1703980001", "ZzTeSt;aLiCe;zzta1703980001"):
        response = import_csv(client, "nom;prenoms;matricule\n" + row + "\n")
        assert response.status_code == 201 and response.json()["unchanged"] == 1, row
    for row in ("zztest;alice", "ZZTEST;ALICE"):  # sans matricule : rapprochement par nom, casse ignorée
        response = import_csv(client, "nom;prenoms\n" + row + "\n")
        assert response.status_code == 201 and response.json()["unchanged"] == 1, row
    assert listing(client)["total"] == 1


# ------------------------------------------------------------------ administration
def test_admin_create_and_update_store_uppercase(client):
    created = client.post("/api/admin_etudiants", json={
        "nom": "zztest", "prenoms": "alice", "matricule": "zzta1703980001", "allergies_affection": "gluten", "filiere": "miage", "niveau": "licence 1"})
    data = created.json()["data"]
    assert created.status_code == 201 and (data["nom"], data["prenoms"], data["allergies_affection"], data["filiere"], data["niveau"]) == (
        "ZZTEST", "ALICE", "GLUTEN", "MIAGE", "LICENCE 1")
    update = client.post("/api/update_etudiant", json={"id": data["id"], "nom": "zztesu", "prenoms": "béatrice", "allergies_affection": "lactose", "niveau": "licence 2"})
    assert update.status_code == 200, update.text
    after = student(client, "ZZTA1703980001")
    assert (after["nom"], after["prenoms"], after["allergies_affection"], after["niveau"]) == ("ZZTESU", "BÉATRICE", "LACTOSE", "LICENCE 2")


def test_search_ignores_case_and_accents_of_the_query_case(client):
    assert import_csv(client, "nom;prenoms;matricule\nZztkouamé;Aline;ZZTA1703980001\n").status_code == 201
    for q in ("zztkouamé", "ZZTKOUAMÉ", "ZztKouaMé", "zzta1703980001", "aline"):
        assert listing(client, q)["total"] == 1, q


# ------------------------------------------------------------------ portail candidat
def test_candidate_requests_are_stored_and_applied_in_uppercase(client):
    base = {"matricule": "zztc0506000001", "nom": "zztcandidat", "prenoms": "carole", "allergies_affection": "arachides", "filiere": "rit", "niveau": "licence 1"}
    assert client.post("/api/demander_creation", json=base).status_code == 201
    pending = [r for r in client.get("/api/admin_creations").json()["data"] if r["matricule_saisi"] == "ZZTC0506000001"][0]
    assert pending["donnees"] == {"nom": "ZZTCANDIDAT", "prenoms": "CAROLE", "allergies_affection": "ARACHIDES", "filiere": "RIT", "niveau": "LICENCE 1"}
    assert client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"}).status_code == 200
    created = student(client, "ZZTC0506000001")
    assert (created["nom"], created["prenoms"], created["allergies_affection"]) == ("ZZTCANDIDAT", "CAROLE", "ARACHIDES")
    # Correction : « même valeur, autre casse » n'est pas une modification
    response = client.post("/api/demander_modification", json={"matricule": "zztc0506000001", "nom": "zztcandidat", "allergies_affection": "Arachides"})
    assert response.status_code == 422 and "Aucune" in response.json()["message"]
    # Vraie correction : stockée et appliquée en majuscules
    assert client.post("/api/demander_modification", json={"matricule": "ZZTC0506000001", "prenoms": "carine", "allergies_affection": "lactose"}).status_code == 201
    request = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == "ZZTC0506000001"][0]
    assert request["modifications"] == {"prenoms": "CARINE", "allergies_affection": "LACTOSE"}
    assert client.post("/api/admin_modifications", json={"id": request["id"], "action": "approve"}).status_code == 200
    final = student(client, "ZZTC0506000001")
    assert (final["prenoms"], final["allergies_affection"]) == ("CARINE", "LACTOSE")


def test_old_pending_requests_in_mixed_case_are_uppercased_on_approval(client):
    """Une demande enregistrée avant la règle (minuscules dans son contenu) est quand même appliquée en majuscules."""
    import json
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute(
            "INSERT INTO etudiant_creation_demande (matricule_saisi, donnees) VALUES ('ZZTO0506000001', %s::jsonb)",
            (json.dumps({"nom": "Zztancien", "prenoms": "Léa", "allergies_affection": "pollen", "filiere": "Rit", "niveau": "Licence 2"}),),
        )
        conn.commit()
    pending = [r for r in client.get("/api/admin_creations").json()["data"] if r["matricule_saisi"] == "ZZTO0506000001"][0]
    assert client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"}).status_code == 200
    data = student(client, "ZZTO0506000001")
    assert (data["nom"], data["prenoms"], data["allergies_affection"], data["filiere"], data["niveau"]) == ("ZZTANCIEN", "LÉA", "POLLEN", "RIT", "LICENCE 2")


# ------------------------------------------------------------------ garantie au niveau de la base
def test_database_converts_to_uppercase_whatever_the_writer(client):
    """Même une écriture directe en SQL (console Neon, script) est mise en majuscules par les déclencheurs."""
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("INSERT INTO etudiant (matricule, nom, prenoms, allergies_affection) VALUES ('zztd0101000001', 'zztkoné', 'éloïse çà', 'œufs')")
        conn.commit()
        row = conn.execute("SELECT matricule, nom, prenoms, allergies_affection FROM etudiant WHERE matricule = 'ZZTD0101000001'").fetchone()
        assert row == ("ZZTD0101000001", "ZZTKONÉ", "ÉLOÏSE ÇÀ", "ŒUFS")
        conn.execute("UPDATE etudiant SET nom = 'zztautre', prenoms = 'jean' WHERE matricule = 'ZZTD0101000001'")
        conn.commit()
        assert conn.execute("SELECT nom, prenoms FROM etudiant WHERE matricule = 'ZZTD0101000001'").fetchone() == ("ZZTAUTRE", "JEAN")
        conn.execute("INSERT INTO filiere (libelle) VALUES ('zztest filiere')")
        conn.commit()
        assert conn.execute("SELECT libelle FROM filiere WHERE libelle ILIKE 'zztest filiere'").fetchone() == ("ZZTEST FILIERE",)
        conn.execute("DELETE FROM filiere WHERE libelle = 'ZZTEST FILIERE'")
        conn.commit()


def test_migration_is_idempotent_and_normalises_existing_rows(client):
    from pathlib import Path
    script = (Path(config.__file__).resolve().parents[1] / "migrations" / "11_majuscules.sql").read_text(encoding="utf-8")
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute(script)  # rejouée : sans effet ni erreur
        conn.commit()
        conn.execute("ALTER TABLE etudiant DISABLE TRIGGER trg_etudiant_majuscules")
        conn.execute("INSERT INTO etudiant (matricule, nom, prenoms) VALUES ('ZZTM0101000001', 'Zztancien', 'Léa')")
        conn.execute("ALTER TABLE etudiant ENABLE TRIGGER trg_etudiant_majuscules")
        conn.commit()
        conn.execute(script)  # une ligne héritée en casse mixte est convertie
        conn.commit()
        assert conn.execute("SELECT nom, prenoms FROM etudiant WHERE matricule = 'ZZTM0101000001'").fetchone() == ("ZZTANCIEN", "LÉA")
