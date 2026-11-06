import logging

import psycopg
from flask import current_app, g
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


def get_db():
    if "db" not in g:
        try:
            g.db = psycopg.connect(
                **current_app.config["DATABASE"],
                row_factory=dict_row,
            )
        except psycopg.Error:
            logger.exception("Connexion à la base UPB impossible")
            raise
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db_app(app):
    app.teardown_appcontext(close_db)
