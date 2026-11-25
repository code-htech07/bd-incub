"""Administration des étudiants : création, modification de TOUS les champs, désinscription.

Données de test : noms commençant par « Zzt », matricules « ZZT… » (nettoyés par la fixture `client`).
"""
import psycopg
import pytest
from fastapi.testclient import TestClient
from test_db_flow import client, import_csv, student  # noqa: F401

from app import config
from app.main import create_app


def create(client, **fields):
    return client.post("/api/admin_etudiants", json={"nom": "Zztest", "prenoms": "Alice", **fields})


def listing(client, q="Zzt"):
    return client.get("/api/get_etudiants", params={"q": q, "per_page": 100}).json()


def register_directly(matricule, number="ZZT-CAND-1"):
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute(
            "INSERT INTO candidat_concours (etudiant_id, numero_candidat) SELECT id, %s FROM etudiant WHERE matricule = %s",
            (number, matricule),
        )
        conn.commit()


# ------------------------------------------------------------------ création
def test_admin_routes_need_authentication(client):
    with TestClient(create_app()) as anonymous:
        assert anonymous.post("/api/admin_etudiants", json={"nom": "Zztest", "prenoms": "Alice"}).status_code == 401
        assert anonymous.post("/api/admin_desinscrire", json={"id": 1}).status_code == 401
        assert anonymous.post("/api/update_etudiant", json={"id": 1, "nom": "Zztest"}).status_code == 401


def test_create_student_only_nom_and_prenoms_required(client):
    response = create(client)
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert created["nom"] == "Zztest" and created["prenoms"] == "Alice" and created["id"]
    assert all(created[k] is None for k in ("matricule", "date_naissance", "filiere", "niveau", "telephone",
                                             "email", "sexe", "allergies_affection", "numero_candidat"))
    assert listing(client)["total"] == 1


def test_create_student_with_every_field(client):
    response = create(
        client, matricule=" zzta1703980001 ", date_naissance="17/03/1998", filiere="RIT", niveau="Licence 2",
        telephone="0701020304", email="alice@exemple.ci", sexe="f", allergies_affection="Arachides",
    )
    assert response.status_code == 201, response.text
    data = student(client, "ZZTA1703980001")
    assert data["nom"] == "Zztest" and data["date_naissance"] == "1998-03-17" and data["filiere"] == "RIT"
    assert data["niveau"] == "Licence 2" and data["sexe"] == "F" and data["telephone"] == "0701020304"
    assert data["email"] == "alice@exemple.ci" and data["allergies_affection"] == "Arachides"


@pytest.mark.parametrize("body,fragment", [
    ({"nom": "", "prenoms": "Alice"}, "obligatoire"),
    ({"nom": "Zztest"}, "obligatoire"),
    ({"nom": "阿里", "prenoms": "Alice"}, "alphabet français"),
    ({"nom": "Zztest", "prenoms": "Jo@n"}, "alphabet français"),
    ({"nom": "Zztest", "prenoms": "Alice", "matricule": "pas-un-matricule"}, "Matricule invalide"),
    ({"nom": "Zztest", "prenoms": "Alice", "matricule": "ZZTA0000000001"}, "Matricule invalide"),
    ({"nom": "Zztest", "prenoms": "Alice", "date_naissance": "31/02/1998"}, "Date de naissance invalide"),
    ({"nom": "Zztest", "prenoms": "Alice", "email": "x"}, "e-mail invalide"),
    ({"nom": "Zztest", "prenoms": "Alice", "sexe": "X"}, "M ou F"),
])
def test_create_student_validation(client, body, fragment):
    response = client.post("/api/admin_etudiants", json=body)
    assert response.status_code == 422, response.text
    assert fragment in response.json()["message"]
    assert listing(client)["total"] == 0


def test_create_student_duplicates(client):
    assert create(client, matricule="ZZTA1703980001").status_code == 201
    response = create(client, prenoms="Autre", matricule="zzta1703980001")
    assert response.status_code == 409 and "matricule existe déjà" in response.json()["message"]
    # Sans matricule : un homonyme exact de l'étudiant existant (accents/casse ignorés) est refusé
    response = client.post("/api/admin_etudiants", json={"nom": "ZZTEST", "prenoms": "alice"})
    assert response.status_code == 409 and "Doublon" in response.json()["message"] and "ZZTA1703980001" in response.json()["message"]
    # Avec un autre matricule : c'est une autre personne
    assert create(client, matricule="ZZTA0205990001").status_code == 201
    assert listing(client)["total"] == 2


# ------------------------------------------------------------------ modification de tous les champs
def test_every_field_can_be_edited_and_cleared(client):
    assert create(client, matricule="ZZTA1703980001", date_naissance="1998-03-17", filiere="RIT",
                  niveau="Licence 2", telephone="0701020304", email="alice@exemple.ci", sexe="F",
                  allergies_affection="Arachides").status_code == 201
    row = listing(client)["data"][0]
    update = lambda **fields: client.post("/api/update_etudiant", json={"id": row["id"], **fields})  # noqa: E731

    response = update(nom="Zztesu", prenoms="Béatrice", matricule="zztb0205990001", date_naissance="02/05/1999",
                      filiere="ASSRI", niveau="Licence 3", telephone="0102030405", email="b@exemple.ci",
                      sexe="m", allergies_affection="Pénicilline")
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"matricule": "ZZTB0205990001"}
    assert client.post("/api/verifier", json={"matricule": "ZZTA1703980001"}).status_code == 404  # ancien matricule libéré
    data = student(client, "ZZTB0205990001")
    assert (data["nom"], data["prenoms"], data["date_naissance"]) == ("Zztesu", "Béatrice", "1999-05-02")
    assert (data["filiere"], data["niveau"], data["telephone"]) == ("ASSRI", "Licence 3", "0102030405")
    assert (data["email"], data["sexe"], data["allergies_affection"]) == ("b@exemple.ci", "M", "Pénicilline")

    # Une chaîne vide efface chaque champ facultatif (date, filière, niveau, téléphone, e-mail, sexe, allergies)
    response = update(date_naissance="", filiere="", niveau="", telephone="", email="", sexe="", allergies_affection="")
    assert response.status_code == 200 and response.json()["data"] == {}
    data = student(client, "ZZTB0205990001")
    assert all(data[k] is None for k in ("date_naissance", "filiere", "niveau", "telephone", "email", "sexe", "allergies_affection"))
    assert data["nom"] == "Zztesu"  # nom et prénoms restent
    # Un seul champ suffit ; renvoyer le même matricule est sans effet
    assert update(telephone="0909090909").status_code == 200
    response = update(matricule="ZZTB0205990001")
    assert response.status_code == 200 and response.json()["message"] == "Fiche inchangée."


def test_edit_validation_and_conflicts(client):
    assert create(client, matricule="ZZTA1703980001").status_code == 201
    assert create(client, prenoms="Bob", matricule="ZZTB0205990001").status_code == 201
    alice = [r for r in listing(client)["data"] if r["prenoms"] == "Alice"][0]
    update = lambda **fields: client.post("/api/update_etudiant", json={"id": alice["id"], **fields})  # noqa: E731
    assert update(matricule="ZZTB0205990001").status_code == 409  # déjà pris par Bob
    assert update(matricule="nope").status_code == 422
    assert update(nom="").status_code == 422 and update(prenoms="   ").status_code == 422
    assert update(nom="阿里").status_code == 422 and update(prenoms="Мария").status_code == 422
    assert update(date_naissance="31/02/1998").status_code == 422
    assert update(email="x").status_code == 422 and update(sexe="X").status_code == 422
    assert update(numero_candidat="N-1").status_code == 422  # pas inscrit
    assert update().status_code == 400
    assert client.post("/api/update_etudiant", json={"id": 999999, "nom": "Zztest"}).status_code == 404
    assert student(client, "ZZTA1703980001")["prenoms"] == "Alice"  # rien n'a été modifié


def test_candidate_number_is_editable_for_registered_students(client):
    assert create(client, matricule="ZZTA1703980001").status_code == 201
    register_directly("ZZTA1703980001", "OFFICIEL-1")
    row = listing(client)["data"][0]
    assert row["numero_candidat"] == "OFFICIEL-1"
    response = client.post("/api/update_etudiant", json={"id": row["id"], "numero_candidat": " OFFICIEL-2 "})
    assert response.status_code == 200, response.text
    assert listing(client)["data"][0]["numero_candidat"] == "OFFICIEL-2"
    assert client.post("/api/update_etudiant", json={"id": row["id"], "numero_candidat": ""}).status_code == 422


# ------------------------------------------------------------------ désinscription
def test_unregister_student_from_the_dashboard(client):
    assert import_csv(client).status_code == 201  # Alice et Bob (matricules ZZT…)
    register_directly("ZZTA1703980001")
    alice = [r for r in listing(client)["data"] if r["matricule"] == "ZZTA1703980001"][0]
    assert alice["numero_candidat"] == "ZZT-CAND-1"
    # Un pointage existant est conservé après désinscription
    assert client.post("/api/pointage", json={"matricule": "ZZTA1703980001", "date": "2026-06-10"}).status_code == 201

    response = client.post("/api/admin_desinscrire", json={"id": alice["id"]})
    assert response.status_code == 200 and response.json()["message"] == "Étudiant désinscrit du concours."
    after = [r for r in listing(client)["data"] if r["matricule"] == "ZZTA1703980001"][0]
    assert after["numero_candidat"] is None and after["nom"] == "Zztest"  # dossier conservé
    assert student(client, "ZZTA1703980001")["prenoms"] == "Alice"
    report = client.get("/api/pointage", params={"date": "2026-06-10"}).json()
    assert [r["matricule"] for r in report["data"]] == ["ZZTA1703980001"]  # historique de pointage conservé
    # Déjà désinscrit / jamais inscrit / identifiant invalide
    assert client.post("/api/admin_desinscrire", json={"id": alice["id"]}).status_code == 404
    bob = [r for r in listing(client)["data"] if r["matricule"] == "ZZTB0205990001"][0]
    assert client.post("/api/admin_desinscrire", json={"id": bob["id"]}).status_code == 404
    assert client.post("/api/admin_desinscrire", json={"id": 0}).status_code == 400
    assert client.post("/api/admin_desinscrire", json={}).status_code == 400


def test_unregistered_student_can_be_registered_again_by_the_official_site(client):
    assert import_csv(client).status_code == 201
    created = client.post("/api/admin_partenaires", json={"nom": "ZZTsite-inscription", "permission": "INSCRIPTION"})
    assert created.status_code == 201, created.text
    key = created.json()["cle"]["valeur"]
    with TestClient(create_app()) as site:
        headers = {"X-API-Key": key}
        # La vérification seule n'inscrit pas
        assert site.post("/api/partenaire/verifier", json={"matricule": "ZZTA1703980001"}, headers=headers).status_code == 200
        assert [r for r in listing(client)["data"] if r["matricule"] == "ZZTA1703980001"][0]["numero_candidat"] is None
        # Création effective du compte : inscription, puis désinscription par l'admin, puis nouvelle inscription
        assert site.post("/api/partenaire/inscrire", json={"matricule": "ZZTA1703980001", "numero_candidat": "OFF-1"}, headers=headers).status_code == 200
        row = [r for r in listing(client)["data"] if r["matricule"] == "ZZTA1703980001"][0]
        assert row["numero_candidat"] == "OFF-1"
        assert client.post("/api/admin_desinscrire", json={"id": row["id"]}).status_code == 200
        again = site.post("/api/partenaire/inscrire", json={"matricule": "ZZTA1703980001", "numero_candidat": "OFF-2"}, headers=headers)
        assert again.json()["inscription"] == {"numero_candidat": "OFF-2", "statut": "EN_ATTENTE", "deja_inscrit": False}


# ------------------------------------------------------------------ traçabilité
def test_student_administration_is_traced(client, monkeypatch):
    monkeypatch.setenv("UPB_AUDIT_ENABLED", "1")

    def events():
        with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
            conn.row_factory = psycopg.rows.dict_row
            return conn.execute("SELECT * FROM journal_activite WHERE ip = 'testclient' ORDER BY id").fetchall()

    def purge():
        with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
            conn.execute("DELETE FROM journal_activite WHERE ip = 'testclient'")
            conn.commit()

    purge()
    try:
        assert create(client, matricule="ZZTA1703980001", email="secret@exemple.ci").status_code == 201
        row = listing(client)["data"][0]
        assert client.post("/api/update_etudiant", json={"id": row["id"], "telephone": "0707070707", "nom": "Zztesu"}).status_code == 200
        register_directly("ZZTA1703980001")
        assert client.post("/api/admin_desinscrire", json={"id": row["id"]}).status_code == 200
        found = {e["action"]: e for e in events()}
        assert found["ETUDIANT_CREE"]["statut"] == "SUCCES" and found["ETUDIANT_CREE"]["acteur"] == "ZZTadmin"
        assert found["ETUDIANT_MODIFIE"]["details"]["champs"] == ["nom", "telephone"]
        assert found["ETUDIANT_DESINSCRIT"]["details"]["numero_candidat"] == "ZZT-CAND-1"
        blob = str(events())
        assert "secret@exemple.ci" not in blob and "0707070707" not in blob  # pas de valeurs personnelles
    finally:
        purge()
