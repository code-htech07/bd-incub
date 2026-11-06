from .admin import admin
from .attendance import attendance
from .students import students


def register_blueprints(app):
    app.register_blueprint(students)
    app.register_blueprint(admin)
    app.register_blueprint(attendance)
