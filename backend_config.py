# backend_config.py
import os

#######################
# CONFIGURAÇÕES GERAIS
#######################
VERSION = "1.0.0"

# URL do backend (caso precise referenciar internamente)
BACKEND_URL = "https://megaboxinterativoled.onrender.com"

# Token de autenticação para acessar a rota /api/v1/salas
SECRET_API_TOKEN = "5up3r53cr3tT0ken!537847349"

#######################
# SINALIZAÇÃO
#######################
# Em produção, você pode definir SIGNALING_URL = BACKEND_URL
# Para testes locais => http://localhost:5000
SIGNALING_URL = os.getenv("SIGNALING_URL", "http://localhost:5000")
AUTH_TOKEN = "segredo123"  # Token simples para kiosk/remote

#######################
# STUN/TURN
#######################
ICE_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"}
    # Se precisar de TURN, adicione aqui
]

#######################
# CÓDECS PREFERIDOS
#######################
CODEC_PREFERENCES = ["video/H264", "video/VP8", "audio/opus"]

#######################
# LOGS
#######################
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE_SERVER = os.path.join(LOG_DIR, "server.log")

#######################
# SERVIDOR FLASK
#######################
PORT = 5000
DEBUG_MODE = False
