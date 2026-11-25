"""E-mails aux étudiants (approbation ou refus d'une demande) via Resend.

Configuration (variables d'environnement) :
  RESEND_API_KEY   clé d'API Resend (sans elle, aucun e-mail n'est envoyé : la demande est traitée normalement)
  UPB_MAIL_FROM    adresse d'expédition (défaut noreply@synapstic.tech) : la SEULE adresse visible par l'étudiant
  UPB_MAIL_NAME    nom affiché de l'expéditeur (défaut « INCUBATEUR UPB »)
  UPB_MAIL_ENABLED 0 pour couper les envois

Une décision unique attend le résultat de l'envoi (2 à 10 s au plus) pour l'afficher à l'administrateur ; un envoi
en masse (« Tout approuver ») part en arrière-plan. Un échec n'annule jamais la décision : il est signalé (réponse ou
journal d'activité, sans clé ni adresse). Aucun Cc, Cci ni Reply-To : le destinataire ne voit que l'expéditeur.
"""
import html
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import audit

logger = logging.getLogger("upb.mail")

DEFAULT_SENDER = "noreply@synapstic.tech"
DEFAULT_NAME = "INCUBATEUR UPB"
RESEND_URL = "https://api.resend.com/emails"
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upb-mail")


def sender() -> str:
    return os.getenv("UPB_MAIL_FROM", "").strip() or DEFAULT_SENDER


def from_header() -> str:
    """« INCUBATEUR UPB <noreply@synapstic.tech> » : le nom affiché, puis l'unique adresse visible."""
    name = re.sub(r'[<>"\r\n,;]', "", os.getenv("UPB_MAIL_NAME", "")).strip() or DEFAULT_NAME
    return f"{name} <{sender()}>"


def enabled() -> bool:
    return bool(os.getenv("RESEND_API_KEY", "").strip()) and os.getenv("UPB_MAIL_ENABLED", "1").strip() != "0"


SUBJECTS = {
    ("creation", True): "Votre dossier a été approuvé",
    ("creation", False): "Votre demande d'ajout de dossier n'a pas été retenue",
    ("correction", True): "Vos modifications ont été approuvées",
    ("correction", False): "Vos modifications n'ont pas été retenues",
}
BODIES = {
    ("creation", True): "Votre demande d'ajout de dossier a été approuvée. Votre dossier étudiant est désormais "
                        "enregistré dans la base I-UPB.",
    ("creation", False): "Votre demande d'ajout de dossier n'a pas été retenue. Vérifiez vos informations (nom, "
                         "prénoms, matricule) puis soumettez une nouvelle demande sur le portail.",
    ("correction", True): "Les modifications demandées sur votre dossier ont été approuvées et appliquées.",
    ("correction", False): "Les modifications demandées sur votre dossier n'ont pas été retenues. Votre dossier "
                           "reste inchangé ; vous pouvez soumettre une nouvelle demande depuis le portail.",
}


def _site_origin() -> str:
    """Premier site autorisé de UPB_CORS_ORIGINS (le site Netlify) ; vide si inconnu."""
    return next((o.strip().rstrip("/") for o in os.getenv("UPB_CORS_ORIGINS", "").split(",") if o.strip()), "")


def _usable(url: str) -> bool:
    """https obligatoire ; http toléré uniquement vers la machine locale (essais en développement)."""
    return url.startswith("https://") or bool(re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", url))


def _allowed_origins() -> list[str]:
    return [o.strip().rstrip("/") for o in os.getenv("UPB_CORS_ORIGINS", "").split(",") if o.strip()]


def portal_url(origin: str | None = None) -> str | None:
    """Page où l'étudiant met à jour ses informations.

    Ordre : `UPB_PORTAL_URL` ; sinon `origin`, le site d'où l'administrateur a pris sa décision (les pages d'administration et
    du portail sont servies par le même site) s'il est utilisable et, quand `UPB_CORS_ORIGINS` est défini, autorisé ;
    sinon la racine du premier site de `UPB_CORS_ORIGINS`."""
    url = os.getenv("UPB_PORTAL_URL", "").strip()
    if not url and origin:
        origin = origin.strip().rstrip("/")
        allowed = _allowed_origins()
        if _usable(origin) and (not allowed or origin in allowed):
            url = origin
    url = url or _site_origin()
    return url if _usable(url) else None


def warn_if_incomplete() -> None:
    """Au démarrage : signale ce qui manquera dans les e-mails (bouton du refus, logo) plutôt que de l'omettre en silence."""
    if not enabled():
        return
    if not portal_url():
        logger.warning("E-MAIL : le bouton « Mettre à jour mes informations » des e-mails de refus est ABSENT : "
                       "définir UPB_PORTAL_URL (adresse https du portail étudiant) ; à défaut, le lien est déduit du site "
                       "d'où l'administrateur refuse la demande.")
    if not logo_url():
        logger.warning("E-MAIL : pas de logo dans l'en-tête : définir UPB_MAIL_LOGO_URL (https) ou UPB_CORS_ORIGINS.")


def logo_url() -> str | None:
    """Adresse publique du logo (les clients de messagerie n'affichent que des images hébergées en https).

    `UPB_MAIL_LOGO_URL`, sinon `logo-upb.png` sur le premier site autorisé de `UPB_CORS_ORIGINS` (le site Netlify)."""
    url = os.getenv("UPB_MAIL_LOGO_URL", "").strip()
    if not url:
        origin = _site_origin()
        url = f"{origin}/logo-upb.png" if origin else ""
    return url if _usable(url) else None


NAVY, GOLD, ROYAL, PAPER = "#1b2275", "#f9b91f", "#1f55d8", "#f6f9ff"
BADGES = {  # (texte, couleur du texte, fond)
    True: ("Approuvé", "#0c6b47", "#dff5ec"),
    False: ("Non retenu", "#9a3412", "#fdebdc"),
}
HEADLINES = {
    ("creation", True): "Votre dossier est approuvé",
    ("creation", False): "Votre demande n'a pas été retenue",
    ("correction", True): "Vos modifications sont approuvées",
    ("correction", False): "Vos modifications n'ont pas été retenues",
}


def compose(
    kind: str, approved: bool, nom: str, prenoms: str, matricule: str | None, comment: str | None = None,
    origin: str | None = None,
) -> tuple[str, str, str]:
    subject = SUBJECTS[(kind, approved)]
    headline = HEADLINES[(kind, approved)]
    name = f"{prenoms} {nom}".strip()
    greeting = f"Bonjour {name}," if name else "Bonjour,"
    body = BODIES[(kind, approved)]
    comment = (comment or "").strip() if not approved else ""
    link = portal_url(origin) if not approved else None
    text = "\n".join(
        [greeting, "", body]
        + ([f"Motif du refus : {comment}"] if comment else [])
        + ([f"Mettre à jour mes informations : {link}"] if link else [])
        + ([f"Matricule : {matricule}"] if matricule else [])
        + ["", "INCUBATEUR UPB", "Ceci est un message automatique : merci de ne pas y répondre."]
    )

    e = html.escape
    badge_text, badge_color, badge_bg = BADGES[approved]
    logo = logo_url()
    logo_cell = (
        f'<td width="56" style="padding-right:14px"><img src="{e(logo, quote=True)}" width="48" height="48" alt="" '
        'style="display:block;width:48px;height:48px;border-radius:12px;background:#ffffff"></td>'
        if logo else ""
    )
    matricule_row = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:22px 0 0;'
        f'background:{PAPER};border:1px solid #dbe5fa;border-radius:12px"><tr><td style="padding:14px 18px">'
        f'<div style="font:700 11px Arial,Helvetica,sans-serif;letter-spacing:1.4px;text-transform:uppercase;'
        f'color:#5b6a96">Matricule</div><div style="font:700 18px Arial,Helvetica,sans-serif;letter-spacing:1px;'
        f'color:{NAVY};padding-top:4px">{e(matricule)}</div></td></tr></table>'
        if matricule else ""
    )
    comment_block = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:22px 0 0;'
        f'background:#fff7ec;border-left:4px solid {GOLD};border-radius:8px"><tr><td style="padding:14px 18px">'
        f'<div style="font:700 11px Arial,Helvetica,sans-serif;letter-spacing:1.4px;text-transform:uppercase;'
        f'color:#9a3412">Motif du refus</div><div style="font:400 15px/1.6 Arial,Helvetica,sans-serif;color:{NAVY};'
        f'padding-top:6px">{e(comment).replace(chr(10), "<br>")}</div></td></tr></table>'
        if comment else ""
    )
    button = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:26px 0 0"><tr>'
        f'<td align="center" bgcolor="{GOLD}" style="background:{GOLD};border-radius:999px">'
        f'<a href="{e(link, quote=True)}" target="_blank" style="display:inline-block;padding:14px 28px;'
        f'font:800 15px Arial,Helvetica,sans-serif;color:{NAVY};text-decoration:none;border-radius:999px">'
        f'Mettre à jour mes informations</a></td></tr></table>'
        if link else ""
    )
    body_html = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{e(subject)}</title></head>
<body style="margin:0;padding:0;background:#e9eefb">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#e9eefb">{e(headline)} — INCUBATEUR UPB</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#e9eefb" style="background:#e9eefb">
<tr><td align="center" style="padding:28px 14px">
 <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #d5def5">
  <tr><td bgcolor="{NAVY}" style="background:{NAVY};padding:22px 28px">
   <table role="presentation" cellpadding="0" cellspacing="0"><tr>{logo_cell}
    <td style="font:800 17px Arial,Helvetica,sans-serif;letter-spacing:3px;color:#ffffff">INCUBATEUR UPB
     <div style="font:600 11px Arial,Helvetica,sans-serif;letter-spacing:1.6px;color:{GOLD};padding-top:4px;text-transform:uppercase">Université Polytechnique de Bingerville</div></td>
   </tr></table>
  </td></tr>
  <tr><td height="4" bgcolor="{GOLD}" style="background:{GOLD};height:4px;line-height:4px;font-size:0">&nbsp;</td></tr>
  <tr><td style="padding:34px 32px 8px">
   <span style="display:inline-block;padding:6px 14px;border-radius:999px;background:{badge_bg};color:{badge_color};font:700 12px Arial,Helvetica,sans-serif;letter-spacing:.8px;text-transform:uppercase">{e(badge_text)}</span>
   <h1 style="margin:16px 0 0;font:800 24px/1.25 Arial,Helvetica,sans-serif;color:{NAVY}">{e(headline)}</h1>
  </td></tr>
  <tr><td style="padding:14px 32px 34px;font:400 15px/1.65 Arial,Helvetica,sans-serif;color:#2b3560">
   <p style="margin:0 0 12px;font-weight:700;color:{NAVY}">{e(greeting)}</p>
   <p style="margin:0">{e(body)}</p>
   {comment_block}
   {matricule_row}
   {button}
  </td></tr>
  <tr><td bgcolor="{PAPER}" style="background:{PAPER};padding:20px 32px;border-top:1px solid #dbe5fa">
   <div style="font:800 12px Arial,Helvetica,sans-serif;letter-spacing:2.4px;color:{NAVY}">INCUBATEUR UPB</div>
   <div style="font:400 12px/1.5 Arial,Helvetica,sans-serif;color:#6b7aa5;padding-top:6px">Ceci est un message automatique : merci de ne pas y répondre.</div>
  </td></tr>
 </table>
</td></tr></table></body></html>"""
    return subject, text, body_html


def _reason(response: httpx.Response) -> str:
    """Motif d'échec renvoyé par Resend (adresses retirées, 160 caractères au plus)."""
    try:
        message = str(response.json().get("message") or "")
    except ValueError:
        message = ""
    message = re.sub(r"\S+@\S+", "…", message).strip()[:160]
    return f"HTTP {response.status_code}" + (f" : {message}" if message else "")


def deliver(to: str, subject: str, text: str, body_html: str) -> tuple[bool, str | None]:
    """Un appel à l'API Resend ; (True, None) si accepté, sinon (False, motif)."""
    try:
        response = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY', '').strip()}"},
            json={"from": from_header(), "to": [to], "subject": subject, "text": text, "html": body_html},
            timeout=10,
        )
    except httpx.HTTPError as error:
        logger.warning("Envoi d'e-mail impossible : %s", type(error).__name__)
        return False, f"Resend injoignable ({type(error).__name__})"
    if response.status_code >= 300:
        reason = _reason(response)
        logger.warning("Envoi d'e-mail refusé par Resend (%s).", reason)
        return False, reason
    return True, None


def _deliver_in_background(to, subject, text, body_html) -> None:
    ok, reason = deliver(to, subject, text, body_html)
    if not ok:
        audit.log("EMAIL_ECHEC", "DEMANDE", status=audit.ECHEC, actor_type="SYSTEME", message=reason,
                  details={"mode": "masse"})


def notify(notice: dict | None, wait: bool = False) -> tuple[str, str | None]:
    """Envoie l'e-mail de décision. Renvoie (état, motif d'échec).

    États : « envoye » / « echec » (`wait=True` : on attend la réponse de Resend), « mis_en_file » (envoi en arrière-plan),
    « sans_email » (aucune adresse connue), « non_configure » (clé absente ou envois coupés)."""
    if not notice:
        return "sans_email", None
    to = (notice.get("to") or "").strip()
    if not _EMAIL.match(to):
        return "sans_email", None
    if not enabled():
        return "non_configure", None
    subject, text, body_html = compose(
        notice["kind"], notice["approved"], notice.get("nom", ""), notice.get("prenoms", ""), notice.get("matricule"),
        notice.get("comment"), notice.get("origin"),
    )
    if wait:
        ok, reason = deliver(to, subject, text, body_html)
        return ("envoye", None) if ok else ("echec", reason)
    if os.getenv("UPB_MAIL_SYNC", "0") == "1":  # tests
        _deliver_in_background(to, subject, text, body_html)
    else:
        _executor.submit(_deliver_in_background, to, subject, text, body_html)
    return "mis_en_file", None
