# backend_server.py
import eventlet
eventlet.monkey_patch()

import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import os

from backend_config import (
    AUTH_TOKEN,
    LOG_FILE_SERVER,
    PORT,
    DEBUG_MODE,
    SECRET_API_TOKEN
)

logger = logging.getLogger("backend.server")
logger.setLevel(logging.INFO)

# RotatingFileHandler para logs
log_handler = RotatingFileHandler(LOG_FILE_SERVER, maxBytes=5_000_000, backupCount=2)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)

app = Flask(__name__)
app.config["SECRET_KEY"] = "secretkey"

# SocketIO com eventlet
socketio = SocketIO(app, cors_allowed_origins="*")

# rooms[room_id] = { "kiosk": sid, "remote": sid }
rooms = {}

@app.route("/")
def index():
    return "BoxInterativa Backend is running..."

@app.route("/api/v1/salas", methods=["GET"])
def api_salas():
    """
    Retorna as salas atuais, mostrando se kiosk e remote estão conectados
    Exige cabeçalho X-Secret-Token = SECRET_API_TOKEN
    """
    token_header = request.headers.get("X-Secret-Token")
    if token_header != SECRET_API_TOKEN:
        return jsonify({"error": "Acesso não autorizado"}), 401
    
    data = {}
    for room_id, mapping in rooms.items():
        data[room_id] = {
            "kiosk": mapping["kiosk"],
            "remote": mapping["remote"]
        }
    return jsonify(data), 200

@socketio.on("connect")
def on_connect():
    sid = request.sid
    logger.info("Novo cliente conectado: %s", sid)

@socketio.on("register")
def on_register(data):
    """
    Exemplo de data:
    {
      'role': 'kiosk' ou 'remote',
      'token': 'segredo123',
      'room_id': 'my-room'
    }
    """
    sid = request.sid
    role = data.get("role")
    token = data.get("token")
    room_id = data.get("room_id", "default-room")

    # Verifica token
    if token != AUTH_TOKEN:
        emit("auth-error", {"error": "Invalid token"}, room=sid)
        return

    # Se a sala não existe, cria
    if room_id not in rooms:
        rooms[room_id] = {"kiosk": None, "remote": None}

    if role == "kiosk":
        rooms[room_id]["kiosk"] = sid
        logger.info("Kiosk registrado -> sala=%s, sid=%s", room_id, sid)
    elif role == "remote":
        rooms[room_id]["remote"] = sid
        logger.info("Remote registrado -> sala=%s, sid=%s", room_id, sid)
    else:
        logger.warning("Role desconhecido: %s", role)

@socketio.on("offer")
def on_offer(msg):
    """
    Kiosk envia 'offer' -> repassa para Remote
    """
    room_id = msg.get("room_id", "default-room")
    if room_id in rooms:
        remote_sid = rooms[room_id]["remote"]
        if remote_sid:
            socketio.emit("offer", msg, room=remote_sid)

@socketio.on("answer")
def on_answer(msg):
    """
    Remote envia 'answer' -> repassa para Kiosk
    """
    room_id = msg.get("room_id", "default-room")
    if room_id in rooms:
        kiosk_sid = rooms[room_id]["kiosk"]
        if kiosk_sid:
            socketio.emit("answer", msg, room=kiosk_sid)

@socketio.on("ice-candidate")
def on_ice_candidate(msg):
    """
    Repasse de ICE candidates entre kiosk e remote
    """
    room_id = msg.get("room_id", "default-room")
    sender = request.sid
    if room_id in rooms:
        kiosk_sid = rooms[room_id]["kiosk"]
        remote_sid = rooms[room_id]["remote"]
        # Se veio do kiosk, manda pro remote
        if sender == kiosk_sid and remote_sid:
            socketio.emit("ice-candidate", msg, room=remote_sid)
        # Se veio do remote, manda pro kiosk
        elif sender == remote_sid and kiosk_sid:
            socketio.emit("ice-candidate", msg, room=kiosk_sid)

@socketio.on("hangup")
def on_hangup(msg):
    """
    Um dos lados encerrou -> notifica o outro
    """
    room_id = msg.get("room_id", "default-room")
    sender = request.sid
    if room_id in rooms:
        kiosk_sid = rooms[room_id]["kiosk"]
        remote_sid = rooms[room_id]["remote"]
        if sender == kiosk_sid and remote_sid:
            socketio.emit("hangup", {}, room=remote_sid)
        elif sender == remote_sid and kiosk_sid:
            socketio.emit("hangup", {}, room=kiosk_sid)

@socketio.on("disconnect")
def on_disconnect():
    """
    Remove o SID das salas que ele ocupava
    """
    sid = request.sid
    for room_id, mapping in rooms.items():
        if mapping["kiosk"] == sid:
            mapping["kiosk"] = None
            logger.info("Kiosk desconectou da sala=%s", room_id)
        if mapping["remote"] == sid:
            mapping["remote"] = None
            logger.info("Remote desconectou da sala=%s", room_id)

if __name__ == "__main__":
    logger.info("Iniciando Backend BoxInterativa na porta=%d", PORT)
    socketio.run(app, host="0.0.0.0", port=PORT, debug=DEBUG_MODE)


