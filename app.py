"""Entry point Flask de ReelForge. Registra blueprints de cada módulo."""
from flask import Flask, render_template

import config
from core import file_utils
from modules.vertical_convert.routes import bp as vertical_convert_bp
from modules.sound_drop.routes import bp as sound_drop_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    file_utils.ensure_dirs()

    # Registro de módulos
    app.register_blueprint(vertical_convert_bp)
    app.register_blueprint(sound_drop_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    return app


app = create_app()


if __name__ == "__main__":
    # Herramienta local: no exponer en red pública.
    app.run(host="127.0.0.1", port=5001, debug=True)
