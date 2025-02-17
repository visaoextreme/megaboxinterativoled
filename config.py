import os

#######################
# CONFIGURAÇÕES GERAIS
#######################
VERSION = "1.0.0"

# Endereço do backend (Render)
BACKEND_URL = "https://megaboxinterativoled.onrender.com"

# Token de autenticação da API para acessar os endpoints (ex.: /api/v1/salas)
SECRET_API_TOKEN = "5up3r53cr3tT0ken!537847349"

#######################
# SINALIZAÇÃO
#######################
# Em produção, você pode configurar a URL real do servidor:
# Exemplo: SIGNALING_URL = BACKEND_URL
# Para testes locais, pode ser "http://localhost:5000"
SIGNALING_URL = os.getenv("SIGNALING_URL", "http://localhost:5000")

AUTH_TOKEN = "segredo123"  # Token simples para kiosk/remote

#######################
# STUN/TURN
#######################
ICE_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"}
    # Se precisar de TURN, configure aqui:
    # {"urls": "turn:meu-servidor-turn:3478", "username": "user", "credential": "pass"}
]

#######################
# CÓDECS PREFERIDOS
#######################
CODEC_PREFERENCES = ["video/H264", "video/VP8", "audio/opus"]

#######################
# RESOLUÇÃO e FPS
#######################
KIOSK_WIDTH = 360
KIOSK_HEIGHT = 640
KIOSK_FPS = 20

REMOTE_WIDTH = 320
REMOTE_HEIGHT = 240
REMOTE_FPS = 20

#######################
# LOGS
#######################
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE_SERVER = os.path.join(LOG_DIR, "server.log")
LOG_FILE_KIOSK = os.path.join(LOG_DIR, "kiosk.log")
LOG_FILE_REMOTE = os.path.join(LOG_DIR, "remote.log")

#######################
# SERVIDOR FLASK
#######################
PORT = 5000
DEBUG_MODE = False

#######################
# MODO SIMULADO
#######################
SIMULATED_MODE = True  # Se True, os clientes usarão vídeos .mp4 (em resources/) em vez de webcam
