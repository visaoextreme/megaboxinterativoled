import eventlet
eventlet.monkey_patch()

import logging
import os
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit

from config import (
    AUTH_TOKEN,
    ICE_SERVERS,
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

# Armazena as salas: rooms[room_id] = {"kiosk": sid, "remote": sid}
rooms = {}

#######################
# API de Gerenciamento
#######################
@app.route('/api/v1/salas', methods=['GET'])
def api_salas():
    token_header = request.headers.get("X-Secret-Token")
    if token_header != SECRET_API_TOKEN:
        return jsonify({"error": "Acesso não autorizado"}), 401
    data = {room_id: {"kiosk": mapping["kiosk"], "remote": mapping["remote"]}
            for room_id, mapping in rooms.items()}
    return jsonify(data)

#######################
# Interface simples de gerenciamento
#######################
@app.route('/manage')
def manage_rooms():
    html = "<h1>Gerenciamento de Salas</h1><ul>"
    for room_id, mapping in rooms.items():
        kiosk_sid = mapping["kiosk"]
        remote_sid = mapping["remote"]
        html += f"<li><b>{room_id}</b>: kiosk={kiosk_sid}, remote={remote_sid}</li>"
    html += "</ul>"
    return render_template_string(html)

@app.route('/')
def index():
    return "Servidor BOXINTERATIVA rodando! Aguardando conexões..."

@socketio.on('connect')
def on_connect():
    sid = eventlet.wsgi.get_current_thread()
    logger.info("[SERVER] Novo cliente conectado. SID=%s", sid)

@socketio.on('register')
def on_register(data):
    """
    data: { 'role': 'kiosk' ou 'remote', 'token': '...', 'room_id': '...' }
    """
    role = data.get('role')
    token = data.get('token')
    room_id = data.get('room_id', "default-room")
    sid = eventlet.wsgi.get_current_thread()

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
    logger.info("[SERVER] Offer na sala=%s -> repassando p/ remote.", room_id)
    if remote_sid:
        socketio.emit('offer', msg, room=remote_sid)

@socketio.on('answer')
def on_answer(msg):
    room_id = msg.get("room_id", "default-room")
    kiosk_sid = rooms.get(room_id, {}).get("kiosk")
    logger.info("[SERVER] Answer na sala=%s -> repassando p/ kiosk.", room_id)
    if kiosk_sid:
        socketio.emit('answer', msg, room=kiosk_sid)

@socketio.on('ice-candidate')
def on_ice_candidate(msg):
    room_id = msg.get("room_id", "default-room")
    sender = eventlet.wsgi.get_current_thread()
    kiosk_sid = rooms[room_id].get("kiosk")
    remote_sid = rooms[room_id].get("remote")
    if sender == kiosk_sid and remote_sid:
        socketio.emit('ice-candidate', msg, room=remote_sid)
    elif sender == remote_sid and kiosk_sid:
        socketio.emit('ice-candidate', msg, room=kiosk_sid)

@socketio.on('hangup')
def on_hangup(msg):
    room_id = msg.get("room_id", "default-room")
    sender = eventlet.wsgi.get_current_thread()
    kiosk_sid = rooms[room_id].get("kiosk")
    remote_sid = rooms[room_id].get("remote")
    if sender == kiosk_sid and remote_sid:
        logger.info("[SERVER] Hangup kiosk->remote, sala=%s", room_id)
        socketio.emit('hangup', {}, room=remote_sid)
    elif sender == remote_sid and kiosk_sid:
        logger.info("[SERVER] Hangup remote->kiosk, sala=%s", room_id)
        socketio.emit('hangup', {}, room=kiosk_sid)

@socketio.on('renegotiate')
def on_renegotiate(msg):
    room_id = msg.get("room_id", "default-room")
    sender = eventlet.wsgi.get_current_thread()
    kiosk_sid = rooms[room_id].get("kiosk")
    remote_sid = rooms[room_id].get("remote")
    if sender == remote_sid and kiosk_sid:
        logger.info("[SERVER] Remote renegotiate->kiosk, sala=%s", room_id)
        socketio.emit('renegotiate', {}, room=kiosk_sid)
    elif sender == kiosk_sid and remote_sid:
        logger.info("[SERVER] Kiosk renegotiate->remote, sala=%s", room_id)
        socketio.emit('renegotiate', {}, room=remote_sid)

@socketio.on('disconnect')
def on_disconnect():
    sid = eventlet.wsgi.get_current_thread()
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
