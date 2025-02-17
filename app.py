import eventlet
eventlet.monkey_patch()

import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit

from config import (
    AUTH_TOKEN,
    LOG_FILE_SERVER,
    PORT,
    DEBUG_MODE,
    VERSION,
    SECRET_API_TOKEN
)

logger = logging.getLogger("boxinterativa.server")
logger.setLevel(logging.INFO)
log_handler = RotatingFileHandler(LOG_FILE_SERVER, maxBytes=5_000_000, backupCount=2)
log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secretkey'
socketio = SocketIO(app, cors_allowed_origins='*')

# Dicionário de salas: rooms[room_id] = {"kiosk": sid, "remote": sid}
rooms = {}

###############################
# API Endpoint para Gerenciamento de Salas
###############################
@app.route('/api/v1/salas', methods=['GET'])
def api_salas():
    token_header = request.headers.get("X-Secret-Token")
    if token_header != SECRET_API_TOKEN:
        return jsonify({"error": "Acesso não autorizado"}), 401
    data = {
        room_id: {
            "kiosk": mapping["kiosk"],
            "remote": mapping["remote"]
        }
        for room_id, mapping in rooms.items()
    }
    return jsonify(data)

###############################
# Interface de Gerenciamento Simples
###############################
@app.route('/manage')
def manage_rooms():
    html = "<h1>Ge
