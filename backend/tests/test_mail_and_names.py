"""Comparaison des noms avec la base et e-mails de décision (Resend simulé : aucun envoi réel).

Données de test : noms « Zzt… », matricules « ZZT… » (nettoyés par la fixture `client`).
"""
import httpx
import pytest
from test_db_flow import client, import_csv, skip_if_real_requests_pending, student  # noqa: F401

from app import mailer
from app.student_rules import best_name_match, name_comparison, name_level, name_similarity


# ------------------------------------------------------------------ règles de ressemblance (sans base)
@pytest.mark.parametrize("a,b,level", [
    (("Kouamé", "Aline Marie"), ("KOUAME", "aline marie"), "identique"),
    (("Konan", "Aline"), ("Konane", "Alyne"), "proche"),          # fautes légères
    (("Konan", "Aline Marie"), ("Konan", "Aline"), "partiel"),  # prénom en moins : plus « identique »
    (("Konan", "Marie Aline"), ("Konan", "Aline Marie"), "identique"),  # ordre des prénoms indifférent
    (("Konan", "Aline"), ("Traore", "Aline"), "different"),
    (("Konan", "Aline"), ("Konan", "Moussa"), "different"),
])
def test_name_similarity_levels(a, b, level):
    assert name_comparison(*a, *b)[1] == level
    assert name_level(name_similarity(*a, *b)) in ("identique", "proche", "different")


def test_best_match_prefers_records_without_matricule_and_respects_birth_dates():
    rows = [
        {"id": 1, "matricule": "ABC010203", "nom": "Konan", "prenoms": "Aline", "date_naissance": None},
        {"id": 2, "matricule": None, "nom": "Konan", "prenoms": "Aline", "date_naissance": None},
        {"id": 3, "matricule": None, "nom": "Konan", "prenoms": "Aline", "date_naissance": "2000-01-01"},
    ]
    assert best_name_match(rows, "Konan", "Aline")[0]["id"] == 2
    assert best_name_match(rows, "Konan", "Aline")[2] == "identique"
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
    assert (match["nom"], match["prenoms"]) == ("ZZTKONAN", "ALINE MARIE")

    response = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["fusion"] is True and data["mail"] == "envoye" and "complété" in data["message"]
    merged = student(client, "ZZTM0506000005")
    assert (merged["nom"], merged["prenoms"]) == ("ZZTKONANE", "ALYNE MARIE")  # version saisie fusionnée
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
    assert student(client, "ZZTM0506000005")["nom"] == "ZZTINCONNU"


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
    assert student(client, "ZZTA1703980001")["nom"] == "ZZTESST"
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
    skip_if_real_requests_pending()
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
    assert response.status_code == 200 and student(client, "ZZTM0506000005")["nom"] == "ZZTPANNE"
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


# ------------------------------------------------------------------ commentaire de refus et bouton de mise à jour
def test_refusal_mail_shows_the_comment_and_the_update_button(monkeypatch):
    monkeypatch.delenv("UPB_PORTAL_URL", raising=False)
    monkeypatch.setenv("UPB_CORS_ORIGINS", "https://concours.exemple.ci/")
    subject, text, page = mailer.compose("creation", False, "Konan", "Aline", None, "Nom illisible.\nMerci de <corriger> & renvoyer.")
    assert "Motif du refus" in page and "Nom illisible.<br>Merci de &lt;corriger&gt; &amp; renvoyer." in page
    assert 'href="https://concours.exemple.ci"' in page and "Mettre à jour mes informations" in page
    assert "Motif du refus : Nom illisible." in text and "Mettre à jour mes informations : https://concours.exemple.ci" in text
    # Sans commentaire : bouton seul ; adresse explicite prioritaire ; http refusé -> pas de bouton
    page = mailer.compose("correction", False, "K", "A", None)[2]
    assert "Motif du refus" not in page and "Mettre à jour mes informations" in page
    monkeypatch.setenv("UPB_PORTAL_URL", "https://portail.exemple.ci/etudiant")
    assert 'href="https://portail.exemple.ci/etudiant"' in mailer.compose("correction", False, "K", "A", None)[2]
    monkeypatch.setenv("UPB_PORTAL_URL", "http://non-securise.ci")
    assert "Mettre à jour mes informations" not in mailer.compose("correction", False, "K", "A", None)[2]
    monkeypatch.delenv("UPB_PORTAL_URL")
    monkeypatch.delenv("UPB_CORS_ORIGINS")
    assert "Mettre à jour mes informations" not in mailer.compose("correction", False, "K", "A", None)[2]  # URL inconnue
    # L'approbation n'a ni bouton ni motif, même si un commentaire est fourni
    monkeypatch.setenv("UPB_CORS_ORIGINS", "https://concours.exemple.ci")
    approved = mailer.compose("creation", True, "K", "A", "ZZTA1703980001", "ne doit pas apparaître")[2]
    assert "Motif du refus" not in approved and "ne doit pas" not in approved and "Mettre à jour" not in approved


def test_admin_comment_is_stored_mailed_and_validated_on_creation_refusal(client, resend, monkeypatch):
    monkeypatch.setenv("UPB_CORS_ORIGINS", "https://concours.exemple.ci")
    pending = submit_creation(client, nom="Zztmotif", prenoms="Luc", email="luc@exemple.ci")
    url = "/api/admin_creations"
    for bad in ("x" * 501, 123, ["a"]):
        response = client.post(url, json={"id": pending["id"], "action": "reject", "commentaire": bad})
        assert response.status_code == 422, bad
    assert [r for r in client.get(url).json()["data"] if r["id"] == pending["id"]]  # toujours en attente
    response = client.post(url, json={"id": pending["id"], "action": "reject", "commentaire": "  Matricule\x00 illisible,   merci de le vérifier.  "})
    assert response.status_code == 200 and response.json()["mail"] == "envoye"
    mail = resend[-1]["json"]
    assert "Matricule illisible, merci de le vérifier." in mail["text"] and "Mettre à jour mes informations" in mail["html"]
    import psycopg
    from app import config
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        stored = conn.execute("SELECT commentaire FROM etudiant_creation_demande WHERE id = %s", (pending["id"],)).fetchone()[0]
    assert stored == "Matricule illisible, merci de le vérifier."


def test_correction_refusal_comment_and_approval_ignores_it(client, resend, monkeypatch):
    monkeypatch.setenv("UPB_CORS_ORIGINS", "https://concours.exemple.ci")
    assert import_csv(client).status_code == 201
    url = "/api/admin_modifications"
    pending = lambda: [r for r in client.get(url).json()["data"] if r["matricule"] == "ZZTA1703980001"][0]  # noqa: E731
    assert client.post("/api/demander_modification", json={"matricule": "ZZTA1703980001", "email": "a@exemple.ci"}).status_code == 201
    assert client.post(url, json={"id": pending()["id"], "action": "reject", "commentaire": "y" * 501}).status_code == 422
    data = client.post(url, json={"id": pending()["id"], "action": "reject", "commentaire": "Adresse e-mail non valide."}).json()
    assert data["mail"] == "envoye" and "Adresse e-mail non valide." in resend[-1]["json"]["text"]
    # Approbation : le commentaire est ignoré (jamais envoyé)
    assert client.post("/api/demander_modification", json={"matricule": "ZZTA1703980001", "email": "b@exemple.ci"}).status_code == 201
    data = client.post(url, json={"id": pending()["id"], "action": "approve", "commentaire": "secret"}).json()
    assert data["mail"] == "envoye" and "secret" not in resend[-1]["json"]["text"] + resend[-1]["json"]["html"]


def test_admin_page_asks_for_a_refusal_comment():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "frontend" / "admin.html").read_text(encoding="utf-8")
    assert 'id="reject-dialog"' in page and 'id="reject-comment" maxlength="500"' in page
    assert page.count("demanderMotifRefus(") >= 3 and "...(commentaire ? { commentaire } : {})" in page


def test_local_addresses_are_accepted_and_missing_portal_url_is_warned(monkeypatch, caplog):
    monkeypatch.delenv("UPB_CORS_ORIGINS", raising=False)
    for url in ("http://localhost:8000", "http://127.0.0.1:5500/"):
        monkeypatch.setenv("UPB_PORTAL_URL", url)
        assert mailer.portal_url() == url
        assert f'href="{url}"' in mailer.compose("creation", False, "K", "A", None)[2]
    for url in ("http://localhost.evil.ci", "http://exemple.ci", "javascript:alert(1)"):
        monkeypatch.setenv("UPB_PORTAL_URL", url)
        assert mailer.portal_url() is None
    # Avertissement au démarrage quand le bouton ou le logo manquera
    monkeypatch.delenv("UPB_PORTAL_URL")
    monkeypatch.delenv("UPB_MAIL_LOGO_URL", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "k")
    with caplog.at_level("WARNING", logger="upb.mail"):
        mailer.warn_if_incomplete()
    assert "UPB_PORTAL_URL" in caplog.text and "UPB_MAIL_LOGO_URL" in caplog.text
    caplog.clear()
    monkeypatch.setenv("UPB_PORTAL_URL", "https://portail.exemple.ci")
    monkeypatch.setenv("UPB_MAIL_LOGO_URL", "https://portail.exemple.ci/logo-upb.png")
    with caplog.at_level("WARNING", logger="upb.mail"):
        mailer.warn_if_incomplete()
    assert caplog.text == ""


# ------------------------------------------------------------------ accusé de réception : délai de 12 h
def test_submission_tells_the_student_about_the_email_and_the_12h_delay(client):
    import psycopg
    from app import config
    # Ajout de dossier : avec adresse
    ok = client.post("/api/demander_creation", json={"matricule": "ZZTM0506000005", "nom": "Zztavis", "prenoms": "Eve", "email": "eve@exemple.ci"})
    body = ok.json()
    assert ok.status_code == 201 and body["notifie_par_email"] is True and body["delai_heures"] == 12
    assert "par e-mail dans un délai de 12 heures" in body["message"]
    # Ajout de dossier : sans adresse
    no_mail = client.post("/api/demander_creation", json={"matricule": "ZZTN0506000006", "nom": "Zztavis", "prenoms": "Paul"}).json()
    assert no_mail["notifie_par_email"] is False and "Aucune adresse e-mail" in no_mail["message"] and "12 heures" not in no_mail["message"]
    # Correction : le dossier n'a pas d'adresse et la demande n'en propose pas -> non notifié
    assert import_csv(client).status_code == 201
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("UPDATE etudiant SET email = NULL WHERE matricule IN ('ZZTA1703980001', 'ZZTB0205990001')")
        conn.commit()
    first = client.post("/api/demander_modification", json={"matricule": "ZZTA1703980001", "telephone": "0555555555"}).json()
    assert first["notifie_par_email"] is False
    # Correction qui ajoute une adresse -> notifié
    second = client.post("/api/demander_modification", json={"matricule": "ZZTB0205990001", "email": "bob@exemple.ci"}).json()
    assert second["notifie_par_email"] is True and "12 heures" in second["message"]
    # Correction d'un dossier qui a déjà une adresse -> notifié sans la renvoyer
    with psycopg.connect(config.database_url(), prepare_threshold=None) as conn:
        conn.execute("UPDATE etudiant SET email = 'deja@exemple.ci' WHERE matricule = 'ZZTA1703980001'")
        conn.commit()
    pending = [r for r in client.get("/api/admin_modifications").json()["data"] if r["matricule"] == "ZZTA1703980001"][0]
    assert client.post("/api/admin_modifications", json={"id": pending["id"], "action": "reject"}).status_code == 200
    third = client.post("/api/demander_modification", json={"matricule": "ZZTA1703980001", "telephone": "0909090909"}).json()
    assert third["notifie_par_email"] is True


def test_portal_page_announces_the_delay():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "data.notifie_par_email" in page and "data.delai_heures" in page and "Renseignez-la pour être informé(e)" in page


# ------------------------------------------------------------------ lien du bouton déduit du site de l'administrateur
def test_button_link_falls_back_to_the_admin_site_origin(monkeypatch):
    for name in ("UPB_PORTAL_URL", "UPB_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    assert mailer.portal_url() is None  # rien de connu
    assert mailer.portal_url("https://concours.exemple.ci/") == "https://concours.exemple.ci"
    assert mailer.portal_url("http://localhost:8000") == "http://localhost:8000"
    assert mailer.portal_url("http://evil.ci") is None and mailer.portal_url("javascript:alert(1)") is None
    page = mailer.compose("creation", False, "K", "A", None, None, "https://concours.exemple.ci")[2]
    assert 'href="https://concours.exemple.ci"' in page and "Mettre à jour mes informations" in page
    # Avec des sites autorisés : l'origine doit en faire partie, sinon repli sur le premier site autorisé
    monkeypatch.setenv("UPB_CORS_ORIGINS", "https://a.exemple.ci,https://b.exemple.ci")
    assert mailer.portal_url("https://b.exemple.ci") == "https://b.exemple.ci"
    assert mailer.portal_url("https://pirate.ci") == "https://a.exemple.ci"
    # L'adresse explicite reste prioritaire
    monkeypatch.setenv("UPB_PORTAL_URL", "https://portail.exemple.ci")
    assert mailer.portal_url("https://b.exemple.ci") == "https://portail.exemple.ci"


def test_refusal_mail_gets_the_link_from_the_admin_request_headers(client, resend, monkeypatch):
    for name in ("UPB_PORTAL_URL", "UPB_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    url = "/api/admin_creations"

    def refuse(headers):
        pending = submit_creation(client, nom="Zztlien", prenoms="Eva", email="eva@exemple.ci")
        assert client.post(url, json={"id": pending["id"], "action": "reject"}, headers=headers).status_code == 200
        return resend[-1]["json"]

    mail = refuse({"Origin": "https://concours.exemple.ci"})
    assert 'href="https://concours.exemple.ci"' in mail["html"] and "https://concours.exemple.ci" in mail["text"]
    mail = refuse({"Referer": "https://autre.exemple.ci/admin.html?x=1"})  # sans Origin : le site du Referer
    assert 'href="https://autre.exemple.ci"' in mail["html"]
    mail = refuse({"Origin": "http://evil.ci"})  # origine inutilisable : pas de bouton
    assert "Mettre à jour mes informations" not in mail["html"]


# ------------------------------------------------------------------ erreur 500 explicite quand la base est en retard
def test_decision_500_names_the_missing_migration(client, monkeypatch):
    import psycopg
    from app import db as database

    def missing_column(*args, **kwargs):
        raise psycopg.errors.UndefinedColumn('column "commentaire" of relation "etudiant_creation_demande" does not exist')

    monkeypatch.setattr(database.Database, "transaction", missing_column)
    for url in ("/api/admin_creations", "/api/admin_modifications"):
        response = client.post(url, json={"id": 1, "action": "reject", "commentaire": "Motif"})
        assert response.status_code == 500, url
        message = response.json()["message"]
        assert message.startswith("Erreur lors du traitement de la demande.") and "scripts.migrate" in message
    # Une autre erreur de base reste générique (aucun détail technique exposé)
    monkeypatch.setattr(database.Database, "transaction", lambda *a, **k: (_ for _ in ()).throw(psycopg.errors.DeadlockDetected("x")))
    response = client.post("/api/admin_creations", json={"id": 1, "action": "approve"})
    assert response.status_code == 500 and response.json()["message"] == "Erreur lors du traitement de la demande."


# ------------------------------------------------------------------ portail : fin de l'envoi
def test_portal_submit_button_stops_spinning_and_the_form_is_cleared():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "frontend"
    page = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "genie.css").read_text(encoding="utf-8")
    start = page.index("function envoiTermine(btn)")
    body = page[start:page.index("function secouer", start)]
    # La classe qui fait tourner le cercle est retirée, le bouton passe à « envoyé », le formulaire est vidé
    assert "classList.remove('loading')" in body and "classList.add('sent')" in body and "viderFormulaire()" in body
    assert "if (response.ok) {\n                    envoiTermine(btn);" in page
    assert ".btn.sent" in css and ".btn.loading::before" in css
    # Une nouvelle vérification restaure le formulaire et le bouton
    reset = page[page.index("function definirMode"):page.index("function setStep")]
    assert "classList.remove('sent', 'loading')" in reset and "profile-fields')" in reset


# ------------------------------------------------------------------ « identique » ne se dit plus d'un nom incomplet
@pytest.mark.parametrize("prenoms", ["FRANCK", "ABEL", "OI", "AMANY FRANCK", "OI AMANY"])
def test_a_subset_of_the_first_names_is_partial_never_identical(prenoms):
    base = ("KOUASSI", "OI AMANY FRANCK ABEL")
    score, level = name_comparison("KOUASSI", prenoms, *base)
    assert level == "partiel" and score >= 0.8
    assert name_comparison("KOUASSI", "OI AMANY FRANCK ABEL", *base) == (1.0, "identique")
    assert name_comparison("kouassi", "abel franck amany oi", *base)[1] == "identique"  # ordre et casse indifférents
    assert name_comparison("KOUASSI", "OI AMANY FRANCK ABELL", *base)[1] == "proche"  # une faute de frappe


def test_partial_match_never_completes_a_record_without_a_confirmed_birth_date():
    rows = [{"id": 1, "matricule": None, "nom": "KOUASSI", "prenoms": "OI AMANY FRANCK ABEL", "date_naissance": None},
            {"id": 2, "matricule": None, "nom": "KOUASSI", "prenoms": "OI AMANY FRANCK ABEL", "date_naissance": "2003-10-07"}]
    # Mode « compléter à l'approbation » : un nom partiel n'est accepté que si les deux dates sont connues et égales
    assert best_name_match(rows[:1], "KOUASSI", "FRANCK", None, without_matricule=True) is None
    assert best_name_match(rows[:1], "KOUASSI", "FRANCK", "2003-10-07", without_matricule=True) is None  # date inconnue en base
    found = best_name_match(rows, "KOUASSI", "FRANCK", "2003-10-07", without_matricule=True)
    assert found[0]["id"] == 2 and found[2] == "partiel"
    # Un nom complet reste complétable sans date
    assert best_name_match(rows[:1], "KOUASSI", "OI AMANY FRANCK ABEL", None, without_matricule=True)[2] == "identique"
    # Hors fusion (simple avertissement), le partiel est signalé mais après les correspondances complètes
    both = rows + [{"id": 3, "matricule": "KOUO0310070001", "nom": "KOUASSI", "prenoms": "FRANCK", "date_naissance": None}]
    assert best_name_match(both, "KOUASSI", "FRANCK")[0]["id"] == 3


def test_creation_request_flags_a_partial_name_and_does_not_merge_it(client):
    created = client.post("/api/admin_etudiants", json={"nom": "ZZTKOUASSI", "prenoms": "OI AMANY FRANCK ABEL", "matricule": "ZZTO0310070001"})
    assert created.status_code == 201, created.text
    # Demande avec un autre matricule et un seul prénom : signalée « partiel » vers le dossier qui a un matricule
    pending = submit_creation(client, matricule="ZZTP0310070002", nom="Zztkouassi", prenoms="Franck")
    match = pending["correspondance"]
    assert match["niveau"] == "partiel" and match["matricule"] == "ZZTO0310070001" and match["a_completer"] is False
    # Sans matricule en base : un nom partiel n'est pas complété sans date de naissance confirmée
    other = client.post("/api/admin_etudiants", json={"nom": "ZZTDAGO", "prenoms": "AYA MARIE JOSEE"})
    assert other.status_code == 201, other.text
    pending = submit_creation(client, matricule="ZZTQ0101000003", nom="Zztdago", prenoms="Aya")
    assert pending["correspondance"]["niveau"] == "partiel" and pending["correspondance"]["a_completer"] is False
    data = client.post("/api/admin_creations", json={"id": pending["id"], "action": "approve"}).json()
    assert data["fusion"] is False  # un nouveau dossier est créé, l'existant n'est pas modifié
    assert student(client, "ZZTQ0101000003")["nom"] == "ZZTDAGO"
    names = [(r["nom"], r["prenoms"], r["matricule"]) for r in client.get("/api/get_etudiants", params={"q": "ZZTDAGO"}).json()["data"]]
    assert ("ZZTDAGO", "AYA MARIE JOSEE", None) in names and ("ZZTDAGO", "AYA", "ZZTQ0101000003") in names
    # Complet : complété sans doublon
    pending = submit_creation(client, matricule="ZZTR0101000004", nom="Zztdago", prenoms="Aya Marie Josee")
    assert pending["correspondance"]["niveau"] == "identique" and pending["correspondance"]["a_completer"] is True


def test_admin_page_words_partial_names_carefully():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "frontend" / "admin.html").read_text(encoding="utf-8")
    assert "partiellement identique" in page and "prénoms en plus ou en moins" in page
