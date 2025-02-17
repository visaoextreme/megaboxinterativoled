import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import socketio
import tkinter as tk
from tkinter import messagebox
import cv2
import pyaudio
import numpy as np
from PIL import Image, ImageTk
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer
)
from aiortc.contrib.media import MediaStreamError
import av
import time
from aiortc.rtcrtpsender import RTCRtpSender
import aiohttp  # para enviar os pings

from config import (
    SIGNALING_URL,
    AUTH_TOKEN,
    ICE_SERVERS,
    CODEC_PREFERENCES,
    KIOSK_WIDTH,
    KIOSK_HEIGHT,
    KIOSK_FPS,
    LOG_FILE_KIOSK,
    SIMULATED_MODE
)

# Configura o logger
log_handler = RotatingFileHandler(LOG_FILE_KIOSK, maxBytes=5_000_000, backupCount=1)
log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
log_handler.setFormatter(log_formatter)
logger = logging.getLogger("boxinterativa.kiosk")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

###############################
# SimulatedVideoTrack para Kiosk
###############################
class SimulatedVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, video_path="resources/box.mp4"):
        super().__init__()
        self.container = av.open(video_path)
        self.video_stream = self.container.streams.video[0]
        self.container.seek(0)

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        for packet in self.container.demux(self.video_stream):
            for frame in packet.decode():
                frm = frame.to_rgb().to_ndarray()
                av_frame = av.VideoFrame.from_ndarray(frm, format="rgb24")
                av_frame.pts = pts
                av_frame.time_base = time_base
                return av_frame
        self.container.seek(0)
        raise MediaStreamError("Loop do vídeo simulado reiniciando...")

###############################
# LocalVideoTrack (Webcam)
###############################
class LocalVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, device_index, width, height, fps):
        super().__init__()
        self.cap = cv2.VideoCapture(device_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Não foi possível acessar a webcam index={device_index} no Kiosk!")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.width = width
        self.height = height
        self._video_muted = False

    def set_video_mute(self, muted: bool):
        self._video_muted = muted

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            raise MediaStreamError("Falha na captura de vídeo no Kiosk")
        if self._video_muted:
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        else:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        av_frame = av.VideoFrame.from_ndarray(frame, format="rgb24")
        av_frame.pts = pts
        av_frame.time_base = time_base
        return av_frame

###############################
# LocalAudioTrack
###############################
class LocalAudioTrack(MediaStreamTrack):
    kind = "audio"
    def __init__(self):
        super().__init__()
        self.pa = pyaudio.PyAudio()
        try:
            self.stream = self.pa.open(format=pyaudio.paInt16,
                                       channels=1,
                                       rate=48000,
                                       input=True,
                                       frames_per_buffer=1024)
        except Exception as e:
            raise RuntimeError(f"Falha ao acessar microfone no Kiosk: {e}")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        data = self.stream.read(1024, exception_on_overflow=False)
        frame = av.AudioFrame.from_ndarray(np.frombuffer(data, dtype=np.int16), layout="mono")
        frame.pts = pts
        frame.time_base = time_base
        return frame

###############################
# RemoteVideoTrack e RemoteAudioTrack
###############################
class RemoteVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, on_frame_callback):
        super().__init__()
        self.on_frame_callback = on_frame_callback

    async def recv(self):
        frame = await super().recv()
        bgr = frame.to_ndarray(format="bgr24")
        self.on_frame_callback(bgr)
        return frame

class RemoteAudioTrack(MediaStreamTrack):
    kind = "audio"
    def __init__(self):
        super().__init__()
        self.pa = pyaudio.PyAudio()
        self.stream_out = self.pa.open(format=pyaudio.paInt16,
                                       channels=1,
                                       rate=48000,
                                       output=True,
                                       frames_per_buffer=1024)

    async def recv(self):
        frame = await super().recv()
        buf = frame.to_ndarray()
        self.stream_out.write(buf.tobytes())
        return frame

###############################
# KioskApp
###############################
class KioskApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BOXINTERATIVA - Caixa (Kiosk)")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", self.exit_fullscreen)

        self.remote_label = tk.Label(self.root, bg="black")
        self.remote_label.pack(fill=tk.BOTH, expand=True)

        self.wait_label = tk.Label(self.remote_label, text="Aguardando Conexão...",
                                   font=("Helvetica", 24, "bold"), fg="white", bg="black")
        self.wait_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self.button_frame = tk.Frame(self.root)
        self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.exit_button = tk.Button(self.button_frame, text="Encerrar", bg="red", fg="white", command=self.on_exit)
        self.exit_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.video_muted = False
        self.mute_video_button = tk.Button(self.button_frame, text="Mute Vídeo", command=self.toggle_video_mute)
        self.mute_video_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.restart_button = tk.Button(self.button_frame, text="Restart Cam", command=self.restart_webcam)
        self.restart_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_label = tk.Label(self.root, text="Status: Normal", bg="grey", fg="white")
        self.status_label.place(x=10, y=10)

        self.quality_label = tk.Label(self.root, text="", bg="black", fg="yellow", font=("Helvetica", 10))
        self.quality_label.place(relx=1.0, y=0, anchor=tk.NE)
        self.last_stats_time = time.time()

        self.sio = socketio.Client(reconnection=False)
        self.pc = None
        self.local_video = None
        self.local_audio = None
        self.room_id = "my-room"
        self.connected = False
        self.call_started = False
        self.answer_timeout_id = None

        self.connect_socketio()
        self.setup_webrtc()

        self.root.after(5000, self.update_stats)

    def connect_socketio(self):
        from config import SIGNALING_URL, AUTH_TOKEN
        try:
            self.sio.connect(SIGNALING_URL)
            self.connected = True
            logger.info("[KIOSK] Conectado ao servidor. Registrando kiosk.")
            self.sio.emit('register', {
                'role': 'kiosk',
                'token': AUTH_TOKEN,
                'room_id': self.room_id
            })
        except Exception as e:
            logger.warning("[KIOSK] Falha ao conectar: %s. Re-tentando em 5s.", e)
            self.connected = False
            self.root.after(5000, lambda: self.connect_socketio())

        @self.sio.event
        def connect():
            logger.info("[KIOSK] Socket.IO connected event.")
            self.connected = True
            self.sio.emit('register', {
                'role': 'kiosk',
                'token': AUTH_TOKEN,
                'room_id': self.room_id
            })
            if not self.call_started:
                asyncio.run_coroutine_threadsafe(self.start_call(), asyncio.get_event_loop())

        @self.sio.event
        def disconnect(data=None):
            logger.warning("[KIOSK] Socket.IO desconectado.")
            self.connected = False
            self.root.after(5000, lambda: self.connect_socketio())

        @self.sio.on('auth-error')
        def on_auth_error(msg):
            messagebox.showerror("Auth Error", f"Token inválido: {msg}")
            self.root.destroy()

        @self.sio.event
        def answer(data):
            logger.info("[KIOSK] Answer recebido do Remote.")
            if self.answer_timeout_id:
                self.root.after_cancel(self.answer_timeout_id)
            desc = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
            asyncio.run_coroutine_threadsafe(self.pc.setRemoteDescription(desc), asyncio.get_event_loop())

        @self.sio.event
        def ice_candidate(data):
            logger.info("[KIOSK] ICE candidate remoto.")
            candidate = {
                "candidate": data["candidate"],
                "sdpMid": data["sdpMid"],
                "sdpMLineIndex": data["sdpMLineIndex"]
            }
            asyncio.run_coroutine_threadsafe(self.pc.addIceCandidate(candidate), asyncio.get_event_loop())

        @self.sio.on('hangup')
        def on_hangup(_=None):
            logger.info("[KIOSK] Hangup recebido, encerrando.")
            messagebox.showinfo("Hangup", "Remote finalizou chamada.")
            self.on_exit()

        @self.sio.on('renegotiate')
        def on_renegotiate(_=None):
            logger.info("[KIOSK] Remote pediu renegotiate, chamando restart_ice.")
            asyncio.run_coroutine_threadsafe(self.restart_ice(), asyncio.get_event_loop())

    def setup_webrtc(self):
        self.init_pc_and_tracks()

    def init_pc_and_tracks(self):
        from config import ICE_SERVERS, CODEC_PREFERENCES, KIOSK_WIDTH, KIOSK_HEIGHT, KIOSK_FPS, SIMULATED_MODE
        self.pc = None
        self.local_video = None
        self.local_audio = None

        from aiortc import RTCConfiguration, RTCIceServer
        rtc_config = RTCConfiguration(iceServers=[RTCIceServer(**server) for server in ICE_SERVERS])
        self.pc = RTCPeerConnection(configuration=rtc_config)

        if SIMULATED_MODE:
            logger.info("[KIOSK] Modo simulado: lendo resources/box.mp4")
            from __main__ import SimulatedVideoTrack
            self.local_video = SimulatedVideoTrack("resources/box.mp4")
        else:
            device_index = self.find_working_camera(20)
            if device_index is None:
                messagebox.showerror("Erro de Mídia", "Nenhuma webcam encontrada no Kiosk!")
                self.root.destroy()
                return
            self.local_video = LocalVideoTrack(device_index, KIOSK_WIDTH, KIOSK_HEIGHT, KIOSK_FPS)

        self.local_audio = LocalAudioTrack()

        vtrans = self.pc.addTransceiver("video", direction="sendrecv")
        atrans = self.pc.addTransceiver("audio", direction="sendrecv")

        self.pc.addTrack(self.local_video)
        self.pc.addTrack(self.local_audio)

        from aiortc.rtcrtpsender import RTCRtpSender
        available_vcodecs = [c for c in RTCRtpSender.getCapabilities("video").codecs if c.mimeType in CODEC_PREFERENCES]
        if available_vcodecs:
            vtrans.setCodecPreferences(available_vcodecs)

        @self.pc.on("track")
        def on_track(track):
            from __main__ import RemoteVideoTrack, RemoteAudioTrack
            if track.kind == "video":
                rv = RemoteVideoTrack(self.update_remote_frame)
                track._queue = rv._queue
            elif track.kind == "audio":
                ra = RemoteAudioTrack()
                track._queue = ra._queue

        @self.pc.on("icecandidate")
        def on_icecandidate(cand):
            if cand and self.connected:
                data = {
                    "candidate": cand["candidate"],
                    "sdpMid": cand["sdpMid"],
                    "sdpMLineIndex": cand["sdpMLineIndex"],
                    "room_id": self.room_id
                }
                self.sio.emit('ice-candidate', data)

        @self.pc.on("iceconnectionstatechange")
        def on_icechange():
            st = self.pc.iceConnectionState
            logger.info("[KIOSK] ICE state=%s", st)
            if st == "failed":
                asyncio.run_coroutine_threadsafe(self.restart_ice(), asyncio.get_event_loop())

    def find_working_camera(self, max_index=20):
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                cap.release()
                return i
        return None

    async def start_call(self):
        if self.call_started or not self.connected:
            return
        self.call_started = True
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        data = {
            "room_id": self.room_id,
            "sdp": offer.sdp,
            "type": offer.type
        }
        self.sio.emit('offer', data)

        def on_timeout():
            messagebox.showwarning("Sem Resposta", "Remote não respondeu em 30s. Tente novamente.")
        self.answer_timeout_id = self.root.after(30000, on_timeout)

    async def restart_ice(self):
        logger.info("[KIOSK] Reiniciando ICE.")
        await self.pc.restartIce()
        new_offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(new_offer)
        data = {
            "room_id": self.room_id,
            "sdp": new_offer.sdp,
            "type": new_offer.type
        }
        self.sio.emit('offer', data)

    def update_remote_frame(self, frame_bgr):
        if self.wait_label.winfo_exists():
            self.wait_label.destroy()
        sw = self.remote_label.winfo_width()
        sh = self.remote_label.winfo_height()
        if sw > 0 and sh > 0:
            frame_bgr = cv2.resize(frame_bgr, (sw, sh))
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame_rgb)
        imgtk = ImageTk.PhotoImage(img)
        self.remote_label.config(image=imgtk)
        self.remote_label.image = imgtk

    def toggle_video_mute(self):
        self.video_muted = not self.video_muted
        if self.local_video and hasattr(self.local_video, "set_video_mute"):
            self.local_video.set_video_mute(self.video_muted)
        self.status_label.config(text=f"Status: Vídeo {'OFF' if self.video_muted else 'ON'}")

    def restart_webcam(self):
        logger.info("[KIOSK] Reiniciando webcam.")
        self.pc.close()
        self.init_pc_and_tracks()
        asyncio.run_coroutine_threadsafe(self.start_call(), asyncio.get_event_loop())

    def update_stats(self):
        if self.pc:
            async def gather_stats():
                stats = await self.pc.getStats()
                inbound_video = [v for k, v in stats.items() if v.type == "inbound-rtp" and v.kind == "video"]
                if inbound_video:
                    inbound = inbound_video[0]
                    br = inbound.bytesReceived
                    now = time.time()
                    dur = now - getattr(self, "last_stats_time", now)
                    self.last_stats_time = now
                    kbps = (br * 8 / 1000) / dur if dur > 0 else 0
                    text = f"Kbps: {kbps:.1f} | lost: {inbound.packetsLost}"
                    self.quality_label.config(text=text)
            asyncio.run_coroutine_threadsafe(gather_stats(), asyncio.get_event_loop())
        self.root.after(5000, self.update_stats)

    def on_exit(self):
        logger.info("[KIOSK] Encerrando kiosk.")
        if self.connected:
            self.sio.emit('hangup', {"room_id": self.room_id})
            self.sio.disconnect()
        if self.pc:
            self.pc.close()
        self.root.destroy()

    def exit_fullscreen(self, event=None):
        self.root.attributes("-fullscreen", False)

###############################
# Funções de Ping para Manter o Servidor Online
###############################
async def check_server_online():
    """Verifica repetidamente se o servidor está online (rota /ping) antes de iniciar."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(SIGNALING_URL + "/ping") as response:
                    if response.status == 200:
                        logger.info("Servidor online.")
                        return
                    else:
                        logger.warning("Ping retornou status %s", response.status)
            except Exception as e:
                logger.warning("Erro no ping: %s", e)
            await asyncio.sleep(5)

async def ping_server():
    """Envia um ping ao servidor a cada 15 segundos para mantê-lo acordado."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(SIGNALING_URL + "/ping") as response:
                    if response.status == 200:
                        logger.info("Ping enviado com sucesso.")
                    else:
                        logger.warning("Ping falhou, status: %s", response.status)
            except Exception as e:
                logger.warning("Erro ao enviar ping: %s", e)
            await asyncio.sleep(15)

###############################
# Ajuste da função main()
###############################
def main():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Antes de iniciar, verifica se o servidor está online
    loop.run_until_complete(check_server_online())
    # Cria uma task para pings periódicos
    loop.create_task(ping_server())

    root = tk.Tk()
    app = KioskApp(root)
    # Cria uma task para atualizar o Tkinter
    async def tk_update():
        while True:
            try:
                root.update()
            except tk.TclError:
                break
            await asyncio.sleep(0.01)
    loop.create_task(tk_update())
    loop.run_forever()

if __name__ == "__main__":
    main()


import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import socketio
import tkinter as tk
from tkinter import messagebox
import cv2
import pyaudio
import numpy as np
from PIL import Image, ImageTk
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer
)
from aiortc.contrib.media import MediaStreamError
import av
import time
from aiortc.rtcrtpsender import RTCRtpSender
import aiohttp  # para os pings

from config import (
    SIGNALING_URL,
    AUTH_TOKEN,
    ICE_SERVERS,
    CODEC_PREFERENCES,
    REMOTE_WIDTH,
    REMOTE_HEIGHT,
    REMOTE_FPS,
    LOG_FILE_REMOTE,
    SIMULATED_MODE
)

log_handler = RotatingFileHandler(LOG_FILE_REMOTE, maxBytes=5_000_000, backupCount=1)
log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
log_handler.setFormatter(log_formatter)
logger = logging.getLogger("boxinterativa.remote")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

###############################
# SimulatedVideoTrack para Remote
###############################
class SimulatedVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, video_path="resources/atriz.mp4"):
        super().__init__()
        self.container = av.open(video_path)
        self.video_stream = self.container.streams.video[0]
        self.container.seek(0)

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        for packet in self.container.demux(self.video_stream):
            for frame in packet.decode():
                frm = frame.to_rgb().to_ndarray()
                av_frame = av.VideoFrame.from_ndarray(frm, format="rgb24")
                av_frame.pts = pts
                av_frame.time_base = time_base
                return av_frame
        self.container.seek(0)
        raise MediaStreamError("Loop do vídeo remoto (atriz) - recomeçando")

###############################
# LocalVideoTrack (Webcam)
###############################
class LocalVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, device_index, width, height, fps, on_frame_callback=None):
        super().__init__()
        self.cap = cv2.VideoCapture(device_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Não foi possível acessar a webcam index={device_index} no Remote!")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.width = width
        self.height = height
        self._video_muted = False
        self.on_frame_callback = on_frame_callback

    def set_video_mute(self, muted: bool):
        self._video_muted = muted

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            raise MediaStreamError("Falha na webcam local (Remote).")
        if self._video_muted:
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        else:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if self.on_frame_callback:
            self.on_frame_callback(frame)
        av_frame = av.VideoFrame.from_ndarray(frame, format="rgb24")
        av_frame.pts = pts
        av_frame.time_base = time_base
        return av_frame

###############################
# LocalAudioTrack
###############################
class LocalAudioTrack(MediaStreamTrack):
    kind = "audio"
    def __init__(self):
        super().__init__()
        self.pa = pyaudio.PyAudio()
        try:
            self.stream = self.pa.open(format=pyaudio.paInt16,
                                       channels=1,
                                       rate=48000,
                                       input=True,
                                       frames_per_buffer=1024)
        except Exception as e:
            raise RuntimeError(f"Falha ao acessar microfone (Remote): {e}")
        self._muted = False

    def set_mute(self, mute: bool):
        self._muted = mute

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        data = self.stream.read(1024, exception_on_overflow=False)
        arr = np.frombuffer(data, dtype=np.int16)
        if self._muted:
            arr[:] = 0
        frame = av.AudioFrame.from_ndarray(arr, layout="mono")
        frame.pts = pts
        frame.time_base = time_base
        return frame

###############################
# RemoteVideoTrack e RemoteAudioTrack
###############################
class RemoteVideoTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, on_frame_callback):
        super().__init__()
        self.on_frame_callback = on_frame_callback

    async def recv(self):
        frame = await super().recv()
        bgr = frame.to_ndarray(format="bgr24")
        self.on_frame_callback(bgr)
        return frame

class RemoteAudioTrack(MediaStreamTrack):
    kind = "audio"
    def __init__(self):
        super().__init__()
        self.pa = pyaudio.PyAudio()
        self.stream_out = self.pa.open(format=pyaudio.paInt16,
                                       channels=1,
                                       rate=48000,
                                       output=True,
                                       frames_per_buffer=1024)

    async def recv(self):
        frame = await super().recv()
        buf = frame.to_ndarray()
        self.stream_out.write(buf.tobytes())
        return frame

###############################
# RemoteApp
###############################
class RemoteApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BOXINTERATIVA - Pessoa Remota")
        self.root.geometry(f"{REMOTE_WIDTH*2}x{max(REMOTE_HEIGHT, 240)+80}")
        self.root.resizable(False, False)

        self.label_local = tk.Label(self.root, bg="gray", text="Minha Webcam")
        self.label_local.place(x=0, y=0, width=REMOTE_WIDTH, height=REMOTE_HEIGHT)

        self.label_remote = tk.Label(self.root, bg="black", text="Vídeo da Caixa")
        self.label_remote.place(x=REMOTE_WIDTH, y=0, width=REMOTE_WIDTH, height=REMOTE_HEIGHT)

        self.wait_label = tk.Label(self.label_remote, text="Aguardando Offer do Kiosk...",
                                   font=("Helvetica", 14, "bold"), fg="white", bg="black")
        self.wait_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self.button_frame = tk.Frame(self.root)
        self.button_frame.place(x=0, y=REMOTE_HEIGHT, width=REMOTE_WIDTH*2, height=80)

        self.exit_button = tk.Button(self.button_frame, text="Encerrar", bg="red", fg="white", command=self.on_exit)
        self.exit_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.mute_button = tk.Button(self.button_frame, text="Mute", bg="gray", fg="white", command=self.toggle_mute)
        self.mute_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.mute_vid_button = tk.Button(self.button_frame, text="Mute Vídeo", bg="gray", command=self.toggle_video_mute)
        self.mute_vid_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_label = tk.Label(self.root, text="Status: Normal", bg="grey", fg="white")
        self.status_label.place(x=10, y=10)

        self.quality_label = tk.Label(self.root, text="", fg="yellow", bg="black")
        self.quality_label.place(relx=1.0, y=0, anchor=tk.NE)
        self.last_stats_time = time.time()

        self.rtc_config = {"iceServers": ICE_SERVERS}
        self.pc = None
        self.local_video = None
        self.local_audio = None

        self.is_muted = False
        self.is_video_muted = False
        self.room_id = "my-room"
        self.sio = socketio.Client(reconnection=False)
        self.connected = False
        self.call_established = False
        self.no_offer_timeout = self.root.after(30000, self.on_no_offer_timeout)

        self.connect_socketio()
        self.setup_webrtc()

        self.root.after(5000, self.update_stats)

    def on_no_offer_timeout(self):
        if not self.call_established:
            messagebox.showwarning("Sem Offer", "Nenhum Kiosk enviou Offer em 30s.")

    def connect_socketio(self):
        from config import SIGNALING_URL, AUTH_TOKEN
        try:
            self.sio.connect(SIGNALING_URL)
            self.connected = True
            logger.info("[REMOTE] Socket.IO conectado.")
            self.sio.emit('register', {
                'role': 'remote',
                'token': AUTH_TOKEN,
                'room_id': self.room_id
            })
        except Exception as e:
            logger.warning("[REMOTE] Falha ao conectar: %s. Retentando em 5s...", e)
            self.connected = False
            self.root.after(5000, lambda: self.connect_socketio())

        @self.sio.event
        def connect():
            logger.info("[REMOTE] on_connect event.")
            self.connected = True
            self.sio.emit('register', {
                'role': 'remote',
                'token': AUTH_TOKEN,
                'room_id': self.room_id
            })

        @self.sio.event
        def disconnect(data=None):
            logger.warning("[REMOTE] Socket.IO desconectado.")
            self.connected = False
            self.root.after(5000, lambda: self.connect_socketio())

        @self.sio.on('auth-error')
        def on_auth_error(msg):
            logger.error("[REMOTE] Auth falhou: %s", msg)
            messagebox.showerror("Auth Error", f"Token inválido: {msg}")
            self.root.destroy()

        @self.sio.event
        def offer(data):
            if self.no_offer_timeout:
                self.root.after_cancel(self.no_offer_timeout)
            logger.info("[REMOTE] Offer recebido do Kiosk.")
            desc = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
            asyncio.run_coroutine_threadsafe(self.handle_offer(desc), asyncio.get_event_loop())

        @self.sio.event
        def ice_candidate(data):
            logger.info("[REMOTE] ICE candidate do Kiosk.")
            candidate = {
                "candidate": data["candidate"],
                "sdpMid": data["sdpMid"],
                "sdpMLineIndex": data["sdpMLineIndex"]
            }
            asyncio.run_coroutine_threadsafe(self.pc.addIceCandidate(candidate), asyncio.get_event_loop())

        @self.sio.on('hangup')
        def on_hangup(_=None):
            logger.info("[REMOTE] Hangup do Kiosk. Encerrando.")
            messagebox.showinfo("Hangup", "Kiosk finalizou a chamada.")
            self.on_exit()

    def setup_webrtc(self):
        self.pc = None
        self.local_video = None
        self.local_audio = None

        from aiortc import RTCConfiguration, RTCIceServer
        rtc_config = RTCConfiguration(iceServers=[RTCIceServer(**server) for server in ICE_SERVERS])
        self.pc = RTCPeerConnection(configuration=rtc_config)

        from config import SIMULATED_MODE
        if SIMULATED_MODE:
            logger.info("[REMOTE] Modo simulado => lendo resources/atriz.mp4")
            from __main__ import SimulatedVideoTrack
            self.local_video = SimulatedVideoTrack("resources/atriz.mp4")
        else:
            cam_index = self.find_working_camera(20)
            if cam_index is None:
                messagebox.showerror("Erro Mídia", "Nenhuma webcam no Remote!")
                self.root.destroy()
                return
            self.local_video = LocalVideoTrack(cam_index, REMOTE_WIDTH, REMOTE_HEIGHT, REMOTE_FPS,
                                               on_frame_callback=self.update_local_frame)

        self.local_audio = LocalAudioTrack()

        vtrans = self.pc.addTransceiver("video", direction="sendrecv")
        atrans = self.pc.addTransceiver("audio", direction="sendrecv")

        self.pc.addTrack(self.local_video)
        self.pc.addTrack(self.local_audio)

        from config import CODEC_PREFERENCES
        from aiortc.rtcrtpsender import RTCRtpSender
        available_vcodecs = [c for c in RTCRtpSender.getCapabilities("video").codecs if c.mimeType in CODEC_PREFERENCES]
        if available_vcodecs:
            vtrans.setCodecPreferences(available_vcodecs)

        @self.pc.on("track")
        def on_track(track):
            if track.kind == "video":
                rv = RemoteVideoTrack(self.update_remote_frame)
                track._queue = rv._queue
            elif track.kind == "audio":
                ra = RemoteAudioTrack()
                track._queue = ra._queue

        @self.pc.on("icecandidate")
        def on_icecandidate(cand):
            if cand and self.connected:
                data = {
                    "room_id": self.room_id,
                    "candidate": cand["candidate"],
                    "sdpMid": cand["sdpMid"],
                    "sdpMLineIndex": cand["sdpMLineIndex"]
                }
                self.sio.emit('ice-candidate', data)

        @self.pc.on("iceconnectionstatechange")
        def on_icechange():
            st = self.pc.iceConnectionState
            logger.info("[REMOTE] ICE state=%s", st)
            if st == "failed":
                asyncio.run_coroutine_threadsafe(self.restart_ice(), asyncio.get_event_loop())

    def find_working_camera(self, max_index=20):
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                cap.release()
                return i
        return None

    async def handle_offer(self, offer):
        await self.pc.setRemoteDescription(offer)
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        data = {
            "room_id": self.room_id,
            "sdp": answer.sdp,
            "type": answer.type
        }
        self.sio.emit('answer', data)
        self.call_established = True

    async def restart_ice(self):
        logger.info("[REMOTE] Reiniciando ICE.")
        await self.pc.restartIce()
        # Se quiser renegociar manualmente:
        # self.sio.emit('renegotiate', {"room_id": self.room_id})

    def update_local_frame(self, frame_bgr):
        rgb = frame_bgr
        img = Image.fromarray(rgb)
        imgtk = ImageTk.PhotoImage(img)
        self.label_local.config(image=imgtk)
        self.label_local.image = imgtk

    def update_remote_frame(self, frame_bgr):
        if self.wait_label and self.wait_label.winfo_exists():
            self.wait_label.destroy()
        rgb = frame_bgr
        img = Image.fromarray(rgb)
        imgtk = ImageTk.PhotoImage(img)
        self.label_remote.config(image=imgtk)
        self.label_remote.image = imgtk

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        self.local_audio.set_mute(self.is_muted)
        self.mute_button.config(text="Unmute" if self.is_muted else "Mute")
        self.status_label.config(text=f"Status: Mic {'OFF' if self.is_muted else 'ON'}")

    def toggle_video_mute(self):
        self.is_video_muted = not self.is_video_muted
        if hasattr(self.local_video, "set_video_mute"):
            self.local_video.set_video_mute(self.is_video_muted)
        self.mute_vid_button.config(text="Unmute Vídeo" if self.is_video_muted else "Mute Vídeo")
        self.status_label.config(text=f"Status: Vídeo {'OFF' if self.is_video_muted else 'ON'}")

    def update_stats(self):
        if self.pc:
            async def gather_stats():
                stats = await self.pc.getStats()
                inbound = [v for k, v in stats.items() if v.type == "inbound-rtp" and v.kind == "video"]
                if inbound:
                    br = inbound[0].bytesReceived
                    now = time.time()
                    dur = now - getattr(self, "last_stats_time", now)
                    self.last_stats_time = now
                    kbps = (br * 8 / 1000) / dur if dur > 0 else 0
                    text = f"{kbps:.1f} kbps, lost={inbound[0].packetsLost}"
                    self.quality_label.config(text=text)
            asyncio.run_coroutine_threadsafe(gather_stats(), asyncio.get_event_loop())
        self.root.after(5000, self.update_stats)

    def on_exit(self):
        logger.info("[REMOTE] Encerrando Remote.")
        if self.connected:
            self.sio.emit('hangup', {"room_id": self.room_id})
            self.sio.disconnect()
        if self.pc:
            self.pc.close()
        self.root.destroy()

###############################
# Funções de Ping para Manter o Servidor Online
###############################
async def check_server_online():
    """Verifica repetidamente se o servidor está online (rota /ping) antes de iniciar."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(SIGNALING_URL + "/ping") as response:
                    if response.status == 200:
                        logger.info("Servidor online.")
                        return
                    else:
                        logger.warning("Ping retornou status %s", response.status)
            except Exception as e:
                logger.warning("Erro no ping: %s", e)
            await asyncio.sleep(5)

async def ping_server():
    """Envia um ping ao servidor a cada 15 segundos para mantê-lo acordado."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(SIGNALING_URL + "/ping") as response:
                    if response.status == 200:
                        logger.info("Ping enviado com sucesso.")
                    else:
                        logger.warning("Ping falhou, status: %s", response.status)
            except Exception as e:
                logger.warning("Erro ao enviar ping: %s", e)
            await asyncio.sleep(15)

###############################
# Ajuste da função main()
###############################
def main():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Aguarda o servidor estar online antes de iniciar
    loop.run_until_complete(check_server_online())
    # Cria uma task para pings periódicos
    loop.create_task(ping_server())

    root = tk.Tk()
    app = RemoteApp(root)
    # Cria uma task para atualizar o Tkinter
    async def tk_update():
        while True:
            try:
                root.update()
            except tk.TclError:
                break
            await asyncio.sleep(0.01)
    loop.create_task(tk_update())
    loop.run_forever()

if __name__ == "__main__":
    main()

# app.py

import os
import logging
from flask import Flask, request, jsonify
from secrets_manager import get_aws_secrets

app = Flask(__name__)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Lemos o SECRET_API_TOKEN do environment (definido no Render ou em .env local)
EXPECTED_TOKEN = os.getenv("SECRET_API_TOKEN", "5up3r53cr3tT0ken!537847349")

@app.route("/")
def index():
    return jsonify({"message": "Backend Megamaxsp rodando com sucesso!"})

@app.route("/get-aws-secrets", methods=["GET"])
def get_aws_secrets_endpoint():
    """
    Retorna todos os secrets do AWS Secrets Manager em formato JSON,
    caso o header Authorization: Bearer <TOKEN> esteja correto.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing or invalid Authorization Bearer token"}), 401

    # Extrai o token do header
    token = auth_header.split("Bearer ")[-1].strip()
    if token != EXPECTED_TOKEN:
        return jsonify({"error": "Invalid token"}), 403

    # Se chegou aqui, token é válido
    secrets_dict = get_aws_secrets()
    # Monta a resposta
    response = {
        "secret": secrets_dict  # Aqui poderia filtrar se quisesse retornar só parte dos segredos
    }
    return jsonify(response), 200

# Se quiser rodar localmente:
if __name__ == "__main__":
    # Exemplo: python app.py -> Sobe em http://localhost:5000
    app.run(host="0.0.0.0", port=5000, debug=True)


TA MUITO AMADOR ESSE PROGAMA O VIDEO DEVE SER 16:9 NA VERTICAL.. SIMPLES ASSIM A JANELA TA ABRINDO GRANDE.. A OUTRA NAO ABRE 16:9 O VIDEO SIMULADO É 16:9 NA VERTICAL..

EU NAO TO VENDO TODOS OS MEUS BUFFETS CONECTADOS...

EU PRECISO DE ALGO ONDE EU ENTRE... VEJA CADA BUFFET CONECADO QUE TEM PAINEL E QUE OPERADOR TA OPERANDO NESSE BUFFET.. SIMPELS ASSIM... SE TA ONLINE OFFLINE

AGORA VO UTE MANDAR MEU BACKEND... N ADA VAI RODAR LOCALMENTE NADA.. É TUDO PELO BACKEND..

AS IMAGENS VAO SER ENVIADAS PARA O BACKEND O BACKEND RETORNA AS IMAGENS PARA QUEM PRECISA DELAS

#app.py

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


config.py do backend

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
# Para testes locais, SIGNALING_URL pode ser "http://localhost:5000"
# Em produção, você pode definir SIGNALING_URL = BACKEND_URL
SIGNALING_URL = os.getenv("SIGNALING_URL", "http://localhost:5000")
AUTH_TOKEN = "segredo123"  # Token simples para kiosk/remote

#######################
# STUN/TURN
#######################
ICE_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"}
    # Se necessário, configure um servidor TURN:
    # {"urls": "turn:seu-turn-servidor:3478", "username": "user", "credential": "pass"}
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


voce via tirar todo script que é desnecessario vai pedir oque te falei vai dar nome diferente pros scripts backend começa com backend o nome
