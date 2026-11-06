from flask import jsonify, request


def json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def method_not_allowed():
    return jsonify(status="error", message="Méthode non autorisée."), 405


def valid_positive_int(value):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    )
