import os

# ----------------------------
# Defina aqui todas as variáveis de configuração
# ----------------------------
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "megabox-123")
LOG_FILE_SERVER = os.environ.get("LOG_FILE_SERVER", "server.log")
PORT = int(os.environ.get("PORT", 5000))
DEBUG_MODE = bool(os.environ.get("DEBUG_MODE", False))
SECRET_API_TOKEN = os.environ.get("SECRET_API_TOKEN", "my-secret-token")

# Para o front-end (kiosk) saber a URL do seu back-end:
SIGNALING_URL = os.environ.get("SIGNALING_URL", "https://megaboxinterativoled.onrender.com")

# STUN/TURN servers
ICE_SERVERS = [{
    "urls": ["stun:stun.l.google.com:19302", "stun:global.stun.twilio.com:3478"]
}]

# Preferências de codecs de vídeo
CODEC_PREFERENCES = ["video/H264", "video/VP8"]
