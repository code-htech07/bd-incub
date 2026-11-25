import pytest


@pytest.fixture(autouse=True)
def _audit_disabled_by_default(monkeypatch):
    """Le journal d'activité écrit dans la base : désactivé sauf dans les tests qui l'exercent."""
    monkeypatch.setenv("UPB_AUDIT_ENABLED", "0")
    monkeypatch.setenv("RESEND_API_KEY", "")  # jamais d'envoi réel pendant les tests
