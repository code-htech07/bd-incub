import logging

import psycopg
from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from .config import Config
from .db import init_db_app
from .routes import register_blueprints

logger = logging.getLogger(__name__)


def create_app(test_config=None):
    app = Flask(__name__, static_folder="../static", static_url_path="")
    app.config.from_object(Config)
    app.config["DATABASE"] = Config.database()
    if test_config:
        app.config.update(test_config)
    app.json.ensure_ascii = False

    register_blueprints(app)
    init_db_app(app)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Requested-With"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, OPTIONS"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return jsonify(status="error", message=error.description), error.code

    @app.errorhandler(psycopg.Error)
    def handle_database_error(error):
        logger.error(
            "Erreur de base de données non gérée",
            exc_info=(type(error), error, error.__traceback__),
        )
        return jsonify(
            status="error",
            message="Erreur de base de données.",
        ), 500

    return app
