import hmac
import os
import re
from functools import wraps

from flask import jsonify, request


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 200
        expected = os.getenv("UPB_ADMIN_TOKEN", "")
        if len(expected) < 10:
            return jsonify(
                status="error",
                message=(
                    "Accès administrateur non configuré. "
                    "Définissez UPB_ADMIN_TOKEN (10 caractères minimum)."
                ),
            ), 503

        authorization = request.headers.get("Authorization", "").strip()
        match = re.fullmatch(r"Bearer\s+(.+)", authorization, flags=re.IGNORECASE)
        if not match or not hmac.compare_digest(expected, match.group(1)):
            return jsonify(
                status="error",
                message="Accès réservé aux administrateurs.",
            ), 401
        return view(*args, **kwargs)

    return wrapped
