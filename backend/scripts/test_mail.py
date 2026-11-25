"""Envoie un e-mail de test pour vérifier la configuration Resend.

Usage (depuis backend/) :  python scripts/test_mail.py adresse@exemple.ci
Affiche l'expéditeur utilisé et le résultat (ou le motif d'échec renvoyé par Resend, ex. domaine non vérifié).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, mailer  # noqa: E402,F401  (config charge le fichier .env)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    print("Expéditeur :", mailer.from_header())
    if not mailer.enabled():
        print("Envois désactivés : RESEND_API_KEY absente ou UPB_MAIL_ENABLED=0.")
        return 1
    state, reason = mailer.notify(
        {"kind": "correction", "approved": True, "to": sys.argv[1], "nom": "Test", "prenoms": "Message", "matricule": None},
        wait=True,
    )
    print("Résultat :", "envoyé" if state == "envoye" else f"{state} ({reason})")
    return 0 if state == "envoye" else 1


if __name__ == "__main__":
    raise SystemExit(main())
