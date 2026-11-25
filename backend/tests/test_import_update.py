"""Réimport d'un CSV : mise à jour des étudiants existants (cellules vides = valeur en base conservée).

Données de test : noms « Zzt… », matricules « ZZT… » (nettoyés par la fixture `client`).
"""
import pytest
from test_db_flow import client, import_csv, student  # noqa: F401

HEADER = "nom;prenoms;matricule;date_naissance;filiere;niveau;telephone;email;sexe;allergies_affection\n"
ALICE = "Zztest;Alice;ZZTA1703980001;17/03/1998;ASSRI;LICENCE 3;0102030405;alice@exemple.ci;F;Arachides\n"
BOB = "Zztest;Bob;ZZTB0205990001;02/05/1999;RIT;LICENCE 1;0707070707;bob@exemple.ci;M;\n"


def listing(client, q="Zzt"):
    return client.get("/api/get_etudiants", params={"q": q, "per_page": 100}).json()


def test_reimport_updates_changed_rows_and_keeps_the_rest(client):
    assert import_csv(client, HEADER + ALICE + BOB).status_code == 201
    changed = ALICE.replace("0102030405", "0909090909").replace("LICENCE 3", "LICENCE 2")
    response = import_csv(client, HEADER + changed + BOB)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["imported"], body["updated"], body["unchanged"]) == (0, 1, 1)
    assert body["message"].startswith("Import terminé : 1 mis à jour, 1 inchangé(s).") and body["matricules"] == ["ZZTA1703980001"]
    alice = student(client, "ZZTA1703980001")
    assert alice["telephone"] == "0909090909" and alice["niveau"] == "LICENCE 2"
    assert alice["email"] == "alice@exemple.ci" and alice["allergies_affection"] == "ARACHIDES"  # inchangés
    assert listing(client)["total"] == 2


def test_empty_cells_keep_the_value_in_database(client):
    assert import_csv(client, HEADER + ALICE).status_code == 201
    # Toutes les cellules facultatives vides : rien n'est effacé, rien n'est modifié
    response = import_csv(client, HEADER + "Zztest;Alice;ZZTA1703980001;;;;;;;\n")
    assert response.status_code == 201 and response.json()["unchanged"] == 1 and response.json()["updated"] == 0
    alice = student(client, "ZZTA1703980001")
    assert (alice["date_naissance"], alice["filiere"], alice["niveau"], alice["telephone"], alice["email"], alice["sexe"], alice["allergies_affection"]) == (
        "1998-03-17", "ASSRI", "LICENCE 3", "0102030405", "alice@exemple.ci", "F", "ARACHIDES")
    # Un fichier qui ne renseigne qu'une colonne ne touche que celle-là
    response = import_csv(client, "nom;prenoms;matricule;email\nZztest;Alice;ZZTA1703980001;nouvelle@exemple.ci\n")
    assert response.json()["updated"] == 1
    alice = student(client, "ZZTA1703980001")
    assert alice["email"] == "nouvelle@exemple.ci" and alice["telephone"] == "0102030405" and alice["filiere"] == "ASSRI"


def test_matricule_row_can_correct_the_spelling_but_not_hijack_another_student(client):
    assert import_csv(client, HEADER + ALICE).status_code == 201
    # Orthographe corrigée (nom proche) : mise à jour du nom
    response = import_csv(client, "nom;prenoms;matricule\nZztestt;Alice;ZZTA1703980001\n")
    assert response.status_code == 201 and response.json()["updated"] == 1
    assert student(client, "ZZTA1703980001")["nom"] == "ZZTESTT"
    # Nom sans rapport : le matricule appartient à quelqu'un d'autre -> erreur, rien n'est modifié
    response = import_csv(client, "nom;prenoms;matricule;telephone\nZzxwvuts;Marc;ZZTA1703980001;0000000000\n")
    assert response.status_code == 422 and "appartient à un autre étudiant" in response.json()["errors"][0]
    assert student(client, "ZZTA1703980001")["telephone"] == "0102030405"


def test_rows_without_matricule_are_matched_by_name_and_birth_date(client):
    assert import_csv(client, HEADER + ALICE + BOB).status_code == 201
    # Même nom, sans matricule : mise à jour de l'étudiant reconnu (les noms ne sont pas réécrits)
    response = import_csv(client, "nom;prenoms;telephone\nZZTEST;alice;0808080808\n")
    assert response.status_code == 201 and (response.json()["updated"], response.json()["imported"]) == (1, 0)
    alice = student(client, "ZZTA1703980001")
    assert alice["telephone"] == "0808080808" and alice["nom"] == "ZZTEST" and alice["prenoms"] == "ALICE"
    # Deux homonymes en base : la ligne sans matricule est ambiguë, sauf si la date de naissance tranche
    assert import_csv(client, HEADER + "Zztest;Alice;ZZTA0205990009;05/05/1990;;;;;;\n").status_code == 201
    response = import_csv(client, "nom;prenoms;telephone\nZztest;Alice;0111111111\n")
    assert response.status_code == 422 and "plusieurs étudiants" in response.json()["errors"][0]
    response = import_csv(client, "nom;prenoms;date_naissance;telephone\nZztest;Alice;05/05/1990;0111111111\n")
    assert response.status_code == 201 and response.json()["updated"] == 1
    assert student(client, "ZZTA0205990009")["telephone"] == "0111111111"
    assert student(client, "ZZTA1703980001")["telephone"] == "0808080808"
    # Date connue et différente : c'est une autre personne, créée
    response = import_csv(client, "nom;prenoms;date_naissance\nZztest;Alice;01/01/1980\n")
    assert response.status_code == 201 and response.json()["imported"] == 1 and response.json()["sans_matricule"] == 1


def test_new_matricule_completes_the_student_without_matricule(client):
    assert import_csv(client, "nom;prenoms\nZztest;Alice\nZztest;Bob\n").status_code == 201
    response = import_csv(client, "nom;prenoms;matricule;email\nZztest;Alice;ZZTA1703980001;alice@exemple.ci\n")
    body = response.json()
    assert response.status_code == 201 and (body["imported"], body["updated"]) == (0, 1) and body["matricules"] == ["ZZTA1703980001"]
    assert student(client, "ZZTA1703980001")["email"] == "alice@exemple.ci"
    assert listing(client)["total"] == 2  # aucun doublon créé
    # Un homonyme qui a déjà un matricule n'est pas complété : un nouveau matricule crée une autre personne
    response = import_csv(client, "nom;prenoms;matricule\nZztest;Alice;ZZTA0205990009\n")
    assert response.json()["imported"] == 1 and listing(client)["total"] == 3
    # Deux étudiants sans matricule de même nom : ambigu
    assert import_csv(client, "nom;prenoms;date_naissance\nZztest;Carl;01/01/1990\nZztest;Carl;02/02/1991\n").status_code == 201
    response = import_csv(client, "nom;prenoms;matricule\nZztest;Carl;ZZTC0101900001\n")
    assert response.status_code == 422 and "plusieurs étudiants" in response.json()["errors"][0]


def test_all_or_nothing_still_applies_with_updates(client):
    assert import_csv(client, HEADER + ALICE).status_code == 201
    bad = HEADER + ALICE.replace("0102030405", "0999999999") + "Zztest;Dina;ZZTD0101900001;31/02/1990;;;;;;\n"
    response = import_csv(client, bad)
    assert response.status_code == 422 and response.json()["errors"][0].startswith("Ligne 3")
    assert student(client, "ZZTA1703980001")["telephone"] == "0102030405"  # la mise à jour valide est annulée
    assert listing(client)["total"] == 1


def test_duplicate_matricule_in_the_same_file_is_still_an_error(client):
    response = import_csv(client, HEADER + ALICE + ALICE)
    assert response.status_code == 422 and "dupliqué dans le fichier" in response.json()["errors"][0]
    assert listing(client)["total"] == 0


def test_import_page_shows_the_update_summary():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "frontend" / "admin.html").read_text(encoding="utf-8")
    assert "data.matricules || []).length" in page and "valeur déjà en base est conservée" in page
