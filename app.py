# app.py
import eventlet
eventlet.monkey_patch()

import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit
import os

from backend_config import (
    AUTH_TOKEN,
    LOG_FILE_SERVER,
    PORT,
    DEBUG_MODE,
    SECRET_API_TOKEN
)

# Configuração do logger
logger = logging.getLogger("boxinterativa.server")
logger.setLevel(logging.INFO)
log_handler = RotatingFileHandler(LOG_FILE_SERVER, maxBytes=5_000_000, backupCount=2)
log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secretkey'
socketio = SocketIO(app, cors_allowed_origins='*')

# Dicionário de salas: cada sala guarda os SIDs de kiosk e remote
rooms = {}

@app.route('/')
def index():
    return "Servidor BOXINTERATIVA rodando! Aguardando conexões..."

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

@app.route('/manage')
def manage_rooms():
    html = "<h1>Gerenciamento de Salas</h1><ul>"
    for room_id, mapping in rooms.items():
        kiosk_sid = mapping["kiosk"]
        remote_sid = mapping["remote"]
        html += f"<li><b>{room_id}</b>: kiosk={kiosk_sid}, remote={remote_sid}</li>"
    html += "</ul>"
    return render_template_string(html)

@socketio.on('connect')
def on_connect():
    sid = request.sid
    logger.info("[SERVER] Novo cliente conectado. SID=%s", sid)

@socketio.on('register')
def on_register(data):
    role = data.get('role')
    token = data.get('token')
    room_id = data.get('room_id', "default-room")
    sid = request.sid

    if token != AUTH_TOKEN:
        logger.warning("[SERVER] Auth falhou. SID=%s, token=%s", sid, token)
        emit('auth-error', {'error': 'Invalid token'}, room=sid)
        return

    if room_id not in rooms:
        rooms[room_id] = {"kiosk": None, "remote": None}

    if role == 'kiosk':
        rooms[room_id]["kiosk"] = sid
        logger.info("[SERVER] Kiosk registrado. sala=%s, SID=%s", room_id, sid)
    elif role == 'remote':
        rooms[room_id]["remote"] = sid
        logger.info("[SERVER] Remote registrado. sala=%s, SID=%s", room_id, sid)
    else:
        logger.warning("[SERVER] Role desconhecido: %s", role)

@socketio.on('offer')
def on_offer(msg):
    room_id = msg.get("room_id", "default-room")
    remote_sid = rooms.get(room_id, {}).get("remote")
    logger.info("[SERVER] Offer na sala=%s -> repassando para remote.", room_id)
    if remote_sid:
        socketio.emit('offer', msg, room=remote_sid)

@socketio.on('answer')
def on_answer(msg):
    room_id = msg.get("room_id", "default-room")
    kiosk_sid = rooms.get(room_id, {}).get("kiosk")
    logger.info("[SERVER] Answer na sala=%s -> repassando para kiosk.", room_id)
    if kiosk_sid:
        socketio.emit('answer', msg, room=kiosk_sid)

@socketio.on('ice-candidate')
def on_ice_candidate(msg):
    room_id = msg.get("room_id", "default-room")
    sender = request.sid
    kiosk_sid = rooms[room_id].get("kiosk")
    remote_sid = rooms[room_id].get("remote")

    if sender == kiosk_sid and remote_sid:
        socketio.emit('ice-candidate', msg, room=remote_sid)
    elif sender == remote_sid and kiosk_sid:
        socketio.emit('ice-candidate', msg, room=kiosk_sid)

@socketio.on('hangup')
def on_hangup(msg):
    room_id = msg.get("room_id", "default-room")
    sender = request.sid
    kiosk_sid = rooms[room_id].get("kiosk")
    remote_sid = rooms[room_id].get("remote")

    if sender == kiosk_sid and remote_sid:
        logger.info("[SERVER] Hangup de kiosk para remote, sala=%s", room_id)
        socketio.emit('hangup', {}, room=remote_sid)
    elif sender == remote_sid and kiosk_sid:
        logger.info("[SERVER] Hangup de remote para kiosk, sala=%s", room_id)
        socketio.emit('hangup', {}, room=kiosk_sid)

@socketio.on('renegotiate')
def on_renegotiate(msg):
    room_id = msg.get("room_id", "default-room")
    sender = request.sid
    kiosk_sid = rooms[room_id].get("kiosk")
    remote_sid = rooms[room_id].get("remote")

    if sender == remote_sid and kiosk_sid:
        logger.info("[SERVER] Remote renegotiate para kiosk, sala=%s", room_id)
        socketio.emit('renegotiate', {}, room=kiosk_sid)
    elif sender == kiosk_sid and remote_sid:
        logger.info("[SERVER] Kiosk renegotiate para remote, sala=%s", room_id)
        socketio.emit('renegotiate', {}, room=remote_sid)

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    logger.info("[SERVER] Cliente desconectou. SID=%s", sid)
    for r_id, mapping in rooms.items():
        if mapping.get("kiosk") == sid:
            mapping["kiosk"] = None
            logger.info("[SERVER] Removido kiosk da sala=%s (disconnect)", r_id)
        if mapping.get("remote") == sid:
            mapping["remote"] = None
            logger.info("[SERVER] Removido remote da sala=%s (disconnect)", r_id)

if __name__ == "__main__":
    logger.info("[SERVER] Iniciando BOXINTERATIVA na porta=%d, debug=%s", PORT, DEBUG_MODE)
    socketio.run(app, host='0.0.0.0', port=PORT, debug=DEBUG_MODE)
