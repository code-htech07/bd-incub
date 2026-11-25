"""Comparaison des noms avec la base et e-mails de décision (Resend simulé : aucun envoi réel).

Données de test : noms « Zzt… », matricules « ZZT… » (nettoyés par la fixture `client`).
"""
import httpx
import pytest
from test_db_flow import client, import_csv, student  # noqa: F401

from app import mailer
from app.student_rules import best_name_match, name_level, name_similarity


# ------------------------------------------------------------------ règles de ressemblance (sans base)
@pytest.mark.parametrize("a,b,level", [
    (("Kouamé", "Aline Marie"), ("KOUAME", "aline marie"), "identique"),
    (("Konan", "Aline"), ("Konane", "Alyne"), "proche"),          # fautes légères
    (("Konan", "Aline Marie"), ("Konan", "Aline"), "identique"),  # prénom en moins : toléré
    (("Konan", "Marie Aline"), ("Konan", "Aline Marie"), "identique"),  # ordre des prénoms indifférent
    (("Konan", "Aline"), ("Traore", "Aline"), "different"),
    (("Konan", "Aline"), ("Konan", "Moussa"), "different"),
])
def test_name_similarity_levels(a, b, level):
    assert name_level(name_similarity(*a, *b)) == level


def test_best_match_prefers_records_without_matricule_and_respects_birth_dates():
    rows = [
        {"id": 1, "matricule": "ABC010203", "nom": "Konan", "prenoms": "Aline", "date_naissance": None},
        {"id": 2, "matricule": None, "nom": "Konan", "prenoms": "Aline", "date_naissance": None},
        {"id": 3, "matricule": None, "nom": "Konan", "prenoms": "Aline", "date_naissance": "2000-01-01"},
    ]
    assert best_name_match(rows, "Konan", "Aline")[0]["id"] == 2
    assert best_name_match(rows, "Konan", "Aline", "2000-01-01")[0]["id"] == 2
    assert best_name_match(rows, "Konan", "Aline", "1999-05-05")[0]["id"] == 2  # id 3 exclu : dates différentes
    assert best_name_match(rows, "Konan", "Aline", without_matricule=True)[0]["id"] == 2
    assert best_name_match(rows[:1], "Konan", "Aline", without_matricule=True) is None
    assert best_name_match(rows, "Zorro", "Aline") is None


# ------------------------------------------------------------------ module d'e-mail
@pytest.fixture
def resend(monkeypatch):
    """Clé factice + envoi synchrone ; capture les appels à l'API Resend."""
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200, json={"id": "x"})

    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("UPB_MAIL_SYNC", "1")
    monkeypatch.delenv("UPB_MAIL_FROM", raising=False)
    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    return sent


def test_mail_states_and_privacy(resend, monkeypatch):
    notice = {"kind": "creation", "approved": True, "to": "eleve@exemple.ci", "nom": "Konan", "prenoms": "Aline", "matricule": "ZZTA1703980001"}
    assert mailer.notify(None)[0] == "sans_email"
    assert mailer.notify({**notice, "to": None})[0] == "sans_email"
    assert mailer.notify({**notice, "to": "pas-un-email"})[0] == "sans_email"
    assert mailer.notify({**notice, "to": "a@b.ci, autre@b.ci"})[0] == "sans_email"  # pas de destinataires multiples
    assert mailer.notify(notice) == ("mis_en_file", None)
    message = resend[0]
    assert message["url"] == "https://api.resend.com/emails" and message["headers"]["Authorization"] == "Bearer re_test_key"
    body = message["json"]
    assert body["from"] == "INCUBATEUR UPB <noreply@synapstic.tech>" and body["to"] == ["eleve@exemple.ci"]
    assert set(body) == {"from", "to", "subject", "text", "html"}  # ni cc, ni bcc, ni reply_to
    assert "Aline Konan" in body["text"] and "ZZTA1703980001" in body["text"]
    assert "@" not in body["text"]  # aucune autre adresse dans le message
    monkeypatch.setenv("RESEND_API_KEY", "")
    assert mailer.notify(notice)[0] == "non_configure" and len(resend) == 1
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("UPB_MAIL_ENABLED", "0")
    assert mailer.notify(notice)[0] == "non_configure" and len(resend) == 1


def test_all_four_messages_differ_and_escape_html():
    subjects = set()
    for kind in ("creation", "correction"):
        for approved in (True, False):
            subject, text, body_html = mailer.compose(kind, approved, "O'<b>Brien", "Aline", None)
            subjects.add(subject)
            assert "<b>" not in body_html and "message automatique" in text
    assert len(subjects) == 4


def test_failed_delivery_never_raises(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: httpx.Response(500))
    assert mailer.deliver("a@b.ci", "s", "t", "<p>t</p>") == (False, "HTTP 500")

    def boom(*a, **k):
        raise httpx.ConnectError("réseau")
    monkeypatch.setattr(mailer.httpx, "post", boom)
    assert mailer.deliver("a@b.ci", "s", "t", "<p>t</p>")[0] is False


# ------------------------------------------------------------------ demandes d'ajout de dossier
def submit_creation(client, **fields):
    payload = {"matricule": "ZZTM0506000005", "nom": "Zztkonan", "prenoms": "Aline Marie", **fields}
    response = client.post("/api/demander_creation", json=payload)
    assert response.status_code == 201, response.text
    return [r for r in client.get("/api/admin_creations").json()["data"] if r["matricule_saisi"] == payload["matricule"]][0]


def test_creation_completes_the_existing_name_only_record_and_emails_the_student(client, resend):
    assert import_csv(client, "nom;prenoms\nZztkonan;Aline Marie\nZztautre;Jean\n").status_code == 201
    pending = submit_creation(client, nom="Zztkonane", prenoms="Alyne Marie", email="aline@exemple.ci", telephone="0102030405")
    match = pending["correspondance"]
    assert match["niveau"] == "proche" and match["a_completer"] is True and match["matricule"] is None
    assert (match["nom"], match["prenoms"]) == ("Zztkonan", "Aline Marie")

    response = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["fusion"] is True and data["mail"] == "envoye" and "complété" in data["message"]
    merged = student(client, "ZZTM0506000005")
    assert (merged["nom"], merged["prenoms"]) == ("Zztkonane", "Alyne Marie")  # version saisie fusionnée
    assert merged["telephone"] == "0102030405" and merged["email"] == "aline@exemple.ci"
    assert client.get("/api/get_etudiants", params={"q": "Zztkon"}).json()["total"] == 1  # aucun doublon
    assert client.get("/api/get_etudiants", params={"q": "Zztautre"}).json()["total"] == 1  # l'autre est intact
    mail = resend[-1]["json"]
    assert mail["to"] == ["aline@exemple.ci"] and mail["from"] == "INCUBATEUR UPB <noreply@synapstic.tech>"
    assert "approuvé" in mail["subject"] and "ZZTM0506000005" in mail["text"]


def test_creation_without_name_in_database_creates_a_new_record(client, resend):
    pending = submit_creation(client, nom="Zztinconnu", prenoms="Paul")
    assert pending["correspondance"] is None
    data = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"}).json()
    assert data["fusion"] is False and data["mail"] == "sans_email" and resend == []  # pas d'adresse : pas d'envoi
    assert student(client, "ZZTM0506000005")["nom"] == "Zztinconnu"


def test_same_name_with_other_matricule_is_flagged_but_not_merged(client, resend):
    assert import_csv(client).status_code == 201  # Zztest Alice (ZZTA1703980001)
    pending = submit_creation(client, nom="Zztest", prenoms="Alice", email="a2@exemple.ci")
    match = pending["correspondance"]
    assert match["matricule"] == "ZZTA1703980001" and match["a_completer"] is False
    data = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"}).json()
    assert data["fusion"] is False  # homonyme avec matricule : jamais écrasé
    assert student(client, "ZZTA1703980001")["email"] != "a2@exemple.ci"
    assert student(client, "ZZTM0506000005")["email"] == "a2@exemple.ci"


def test_creation_refusal_emails_the_candidate(client, resend):
    pending = submit_creation(client, nom="Zztrefus", prenoms="Luc", email="luc@exemple.ci")
    data = client.post("/api/admin_creations", json={"id": pending["id"], "action": "reject"}).json()
    assert data["message"] == "Demande refusée." and data["mail"] == "envoye"
    mail = resend[-1]["json"]
    assert mail["to"] == ["luc@exemple.ci"] and "pas été retenue" in mail["subject"]
    assert client.post("/api/verifier", json={"matricule": "ZZTM0506000005"}).status_code == 404  # rien créé


# ------------------------------------------------------------------ demandes de correction
def test_correction_shows_name_check_and_emails_on_both_decisions(client, resend):
    assert import_csv(client).status_code == 201
    url = "/api/demander_modification"
    # Faute légère sur le nom + nouvelle adresse
    assert client.post(url, json={"matricule": "ZZTA1703980001", "nom": "Zztesst", "email": "nouveau@exemple.ci"}).status_code == 201
    request = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == "ZZTA1703980001"][0]
    assert request["verification_noms"]["niveau"] == "proche"
    data = client.post("/api/admin_modifications", json={"id": request["id"], "action": "approve"}).json()
    assert data["mail"] == "envoye"
    assert resend[-1]["json"]["to"] == ["nouveau@exemple.ci"] and "approuvées" in resend[-1]["json"]["subject"]
    assert student(client, "ZZTA1703980001")["nom"] == "Zztesst"
    # Nom sans rapport : signalé « different » ; refus -> mail à l'adresse connue du dossier
    assert client.post(url, json={"matricule": "ZZTA1703980001", "nom": "Zzxwvuts"}).status_code == 201
    request = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == "ZZTA1703980001"][0]
    assert request["verification_noms"]["niveau"] == "different"
    data = client.post("/api/admin_modifications", json={"id": request["id"], "action": "reject"}).json()
    assert data["mail"] == "envoye"
    assert resend[-1]["json"]["to"] == ["nouveau@exemple.ci"] and "pas été retenues" in resend[-1]["json"]["subject"]
    # Correction sans nom : pas de vérification de noms
    assert client.post(url, json={"matricule": "ZZTA1703980001", "telephone": "0101010101"}).status_code == 201
    request = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == "ZZTA1703980001"][0]
    assert request["verification_noms"] is None


def test_approve_all_sends_one_mail_per_request(client, resend):
    assert import_csv(client).status_code == 201
    assert client.post("/api/demander_modification", json={"matricule": "ZZTA1703980001", "email": "a@exemple.ci"}).status_code == 201
    submit_creation(client, nom="Zztnouveau", prenoms="Eve", email="eve@exemple.ci")
    before = len(resend)
    assert client.post("/api/admin_approve_all").status_code == 200
    assert sorted(m["json"]["to"][0] for m in resend[before:]) == ["a@exemple.ci", "eve@exemple.ci"]


def test_decision_succeeds_even_if_mail_fails(client, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("UPB_MAIL_SYNC", "1")
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: httpx.Response(500))
    pending = submit_creation(client, nom="Zztpanne", prenoms="Max", email="max@exemple.ci")
    response = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"})
    assert response.status_code == 200 and student(client, "ZZTM0506000005")["nom"] == "Zztpanne"
    assert response.json()["mail"] == "echec" and response.json()["mail_erreur"] == "HTTP 500"  # signalé à l'admin


def test_admin_page_shows_name_checks_and_mail_state():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "frontend" / "admin.html").read_text(encoding="utf-8")
    assert "noteNoms(request, true)" in page and "noteNoms(request, false)" in page and "texteMail(data)" in page


def test_display_name_is_incubateur_upb_and_only_one_address_is_visible(resend, monkeypatch):
    notice = {"kind": "correction", "approved": True, "to": "eleve@exemple.ci", "nom": "Konan", "prenoms": "Aline", "matricule": None}
    assert mailer.notify(notice, wait=True) == ("envoye", None)
    body = resend[-1]["json"]
    assert body["from"] == "INCUBATEUR UPB <noreply@synapstic.tech>"
    assert "INCUBATEUR UPB" in body["text"] and "INCUBATEUR UPB" in body["html"]
    # Le nom affiché est nettoyé (aucune injection d'en-tête) ; l'adresse reste la seule adresse de l'expéditeur
    monkeypatch.setenv("UPB_MAIL_NAME", 'Pirate <x@evil.ci>\r\nBcc: y@evil.ci')
    header = mailer.from_header()
    assert "\n" not in header and "<" not in header.rsplit("<", 1)[0] and header.endswith("<noreply@synapstic.tech>")


def test_waiting_delivery_reports_the_reason_and_strips_addresses(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    notice = {"kind": "creation", "approved": False, "to": "eleve@exemple.ci", "nom": "K", "prenoms": "A", "matricule": None}
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: httpx.Response(
        403, json={"name": "validation_error", "message": "The synapstic.tech domain is not verified for eleve@exemple.ci."}))
    state, reason = mailer.notify(notice, wait=True)
    assert state == "echec" and reason.startswith("HTTP 403 : The synapstic.tech domain is not verified")
    assert "eleve@exemple.ci" not in reason


def test_background_failure_is_written_to_the_journal(monkeypatch):
    logged = []
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("UPB_MAIL_SYNC", "1")
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: httpx.Response(401, json={"message": "API key is invalid"}))
    monkeypatch.setattr(mailer.audit, "log", lambda *a, **k: logged.append((a, k)))
    notice = {"kind": "creation", "approved": True, "to": "eleve@exemple.ci", "nom": "K", "prenoms": "A", "matricule": None}
    assert mailer.notify(notice) == ("mis_en_file", None)
    assert logged and logged[0][0][0] == "EMAIL_ECHEC" and logged[0][1]["actor_type"] == "SYSTEME"
    assert logged[0][1]["message"] == "HTTP 401 : API key is invalid"


def test_html_message_design_logo_and_safety(monkeypatch):
    for name in ("UPB_MAIL_LOGO_URL", "UPB_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    _, _, page = mailer.compose("creation", True, "Konan", "Aline", "ZZTA1703980001")
    assert "<img" not in page and "INCUBATEUR UPB" in page and "Approuvé" in page and "ZZTA1703980001" in page
    assert 'role="presentation"' in page and 'lang="fr"' in page and "<script" not in page
    # Logo : adresse explicite, sinon déduite du premier site autorisé (https uniquement)
    monkeypatch.setenv("UPB_CORS_ORIGINS", "https://concours.exemple.ci/, https://autre.ci")
    assert mailer.logo_url() == "https://concours.exemple.ci/logo-upb.png"
    assert 'src="https://concours.exemple.ci/logo-upb.png"' in mailer.compose("creation", True, "K", "A", None)[2]
    monkeypatch.setenv("UPB_MAIL_LOGO_URL", "https://cdn.exemple.ci/l.png")
    assert mailer.logo_url() == "https://cdn.exemple.ci/l.png"
    monkeypatch.setenv("UPB_MAIL_LOGO_URL", "http://non-securise.ci/l.png")
    assert mailer.logo_url() is None
    monkeypatch.setenv("UPB_MAIL_LOGO_URL", 'https://x.ci/"><script>')
    assert "<script" not in mailer.compose("creation", True, "K", "A", None)[2]
    # Refus : autre badge ; sans matricule : pas de bloc matricule
    refused = mailer.compose("correction", False, "K", "A", None)[2]
    assert "Non retenu" in refused and "Matricule" not in refused
