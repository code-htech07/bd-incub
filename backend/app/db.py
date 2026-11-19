"""Connexion PostgreSQL (Neon) : pool de connexions partagé + dépendance FastAPI."""
import logging
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def open_pool() -> None:
    """Ouvre le pool sans bloquer : une base injoignable n'empêche pas le démarrage."""
    global _pool
    if _pool is not None:
        return
    _pool = ConnectionPool(
        conninfo=config.database_url(),
        min_size=1,
        max_size=5,
        max_idle=60,  # recycle les connexions inactives avant que Neon / le pooler ne les coupe
        max_lifetime=900,
        open=False,
        check=ConnectionPool.check_connection,  # Neon coupe les connexions inactives
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": config.connect_timeout(),
            "prepare_threshold": None,  # compatible avec le pooler Neon (PgBouncer)
        },
    )
    _pool.open(wait=False)


_audit_pool: ConnectionPool | None = None


def audit_connection():
    """Connexion issue d'un petit pool DÉDIÉ au journal d'activité (jamais le pool des requêtes :
    pas d'interblocage possible, et l'écriture n'est pas liée à la transaction de la requête)."""
    global _audit_pool
    if _audit_pool is None:
        _audit_pool = ConnectionPool(
            conninfo=config.database_url(),
            min_size=0,
            max_size=2,
            open=False,
            check=ConnectionPool.check_connection,
            kwargs={"connect_timeout": config.connect_timeout(), "prepare_threshold": None},
        )
        _audit_pool.open(wait=False)
    return _audit_pool.connection(timeout=config.connect_timeout())


def close_pool() -> None:
    global _pool, _audit_pool
    if _pool is not None:
        _pool.close()
        _pool = None
    if _audit_pool is not None:
        _audit_pool.close()
        _audit_pool = None


class Database:
    """Accès paresseux à une connexion du pool : rien n'est emprunté tant que la
    route n'interroge pas la base (les validations d'entrée n'en dépendent donc pas)."""

    def __init__(self) -> None:
        self._manager = None
        self._connection: psycopg.Connection | None = None

    def _get(self) -> psycopg.Connection:
        if self._connection is None:
            if _pool is None:
                open_pool()
            assert _pool is not None
            self._manager = _pool.connection(timeout=config.connect_timeout())
            self._connection = self._manager.__enter__()
        return self._connection

    def cursor(self, *args, **kwargs):
        return self._get().cursor(*args, **kwargs)

    def transaction(self):
        return self._get().transaction()

    def release(self) -> None:
        if self._manager is not None:
            self._manager.__exit__(None, None, None)
            self._manager = None
            self._connection = None


def get_db() -> Iterator[Database]:
    database = Database()
    try:
        yield database
    finally:
        database.release()


# --------------------------------------------------------------------------- version du schéma
# Une migration est considérée comme appliquée si l'objet qu'elle crée existe. Sert à détecter une base
# (ex. de production) restée en retard après une mise à jour du code.
MIGRATION_PROBES: tuple[tuple[str, str, str | None, str | None], ...] = (
    # (fichier, table, colonne à exiger, "nullable" pour exiger une colonne facultative)
    ("01_pointage.sql", "pointage_journalier", None, None),
    ("02_etudiant_contact.sql", "etudiant", "email", None),
    ("03_inscription_migrations.sql", "etudiant_modification", None, None),
    ("04_etudiant_creation_demande.sql", "etudiant_creation_demande", None, None),
    ("05_administrateur.sql", "administrateur", None, None),
    ("06_partenaire.sql", "partenaire", None, None),
    ("07_journal_activite.sql", "journal_activite", None, None),
    ("08_integrations.sql", "partenaire_cle", None, None),
    ("09_matricule_facultatif.sql", "etudiant", "matricule", "nullable"),
)


def schema_problems(connection) -> list[str]:
    """Fichiers de migration manquants (liste vide : schéma à jour)."""
    rows = connection.execute(
        "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema()"
    ).fetchall()
    columns = {(r[0], r[1]): r[2] for r in rows}
    tables = {r[0] for r in rows}
    missing = []
    for filename, table, column, require in MIGRATION_PROBES:
        if table not in tables:
            missing.append(filename)
        elif column and (table, column) not in columns:
            missing.append(filename)
        elif require == "nullable" and columns.get((table, column)) != "YES":
            missing.append(filename)
    return missing


SCHEMA_HELP = (
    "La base de données n'est pas à jour : appliquez les migrations (python -m scripts.migrate) "
    "sur cette base."
)


def schema_hint(error: BaseException) -> str | None:
    """Message clair si l'erreur vient d'un schéma en retard (colonne/table absente, NULL refusé)."""
    if isinstance(
        error,
        (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.NotNullViolation),
    ):
        return SCHEMA_HELP
    return None


def log_schema_status() -> None:
    """Journalise (sans bloquer le démarrage) les migrations manquantes."""
    try:
        with audit_connection() as connection:
            missing = schema_problems(connection)
        if missing:
            logger.error("SCHÉMA DE BASE OBSOLÈTE — migrations manquantes : %s. %s", ", ".join(missing), SCHEMA_HELP)
        else:
            logger.info("Schéma de base de données à jour.")
    except Exception:  # noqa: BLE001
        logger.exception("Contrôle du schéma impossible (base injoignable ?)")
