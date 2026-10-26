#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCANPA - Escáner WiFi con ESP32 + interfaz gráfica web (estilo glass).

Cómo se usa:
    python scanpa.py

Al arrancar:
  1. Instala solo lo necesario (pyserial y mpremote) si faltan.
  2. Abre un servidor local en http://127.0.0.1:8765 y lanza el navegador.
  3. Todo se controla desde la interfaz: iniciar/detener, histórico, ajustes.

Carpetas (junto a este fichero):
    log_csv/             -> log CSV por sesión
    log_txt/             -> log TXT por sesión
    logo/                -> foto de perfil
    data/                -> (se crea sola) config.json y el histórico interno (.jsonl)
"""

import ast
import csv
import importlib
import importlib.util
import json
import mimetypes
import os
import random
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# ---------- Rutas ----------

BASE = os.path.dirname(os.path.abspath(__file__))
# Los ficheros de la interfaz (index.html, style.css, app.js) pueden estar junto a este .py
# o dentro de una carpeta "web". Solo se sirven extensiones de la lista blanca.
WEB = os.path.join(BASE, "web") if os.path.isdir(os.path.join(BASE, "web")) else BASE
EXT_WEB = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
DATA = os.path.join(BASE, "data")
DIR_SCANS = os.path.join(DATA, "scans")
DIR_TXT = os.path.join(BASE, "log_txt")
DIR_CSV = os.path.join(BASE, "log_csv")
DIR_AVATAR = os.path.join(BASE, "logo")
CONFIG_FILE = os.path.join(DATA, "config.json")
OUI_LOCAL = os.path.join(BASE, "oui_local.json")

HOST = os.environ.get("SCANPA_HOST", "127.0.0.1")  # 0.0.0.0 para verlo desde el móvil (sin contraseña!)
PUERTO_WEB = int(os.environ.get("SCANPA_PORT", "8765"))

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # evita que parpadee una consola en Windows

# ---------- Constantes del escáner ----------

SEGURIDAD = {
    0: "Abierta (sin contraseña)",
    1: "WEP",
    2: "WPA-PSK",
    3: "WPA2-PSK",
    4: "WPA/WPA2-PSK",
    5: "WPA2-Enterprise",
    6: "WPA3-PSK",
    7: "WPA2/WPA3-PSK",
}

# Rellénala a mano si quieres: "AA:BB:CC" (mayúsculas) -> nombre. Tiene prioridad sobre oui_local.json
FABRICANTES_LOCALES = {
    # "F8:0B:BE": "Movistar (router HGU)",
    # "AC:9B:0A": "Vodafone (router)",
}

COD_ESCANEO = (
    "import network; sta = network.WLAN(network.STA_IF); "
    "sta.active(True); print(sta.scan())"
)

# Fabricantes de chips USB-serie típicos de placas ESP32 (CP210x, CH340, FTDI, Espressif nativo)
VIDS_ESP = {0x10C4, 0x1A86, 0x0403, 0x303A}

DEPENDENCIAS = [("serial", "pyserial"), ("mpremote", "mpremote")]

CONFIG_DEFECTO = {
    "nombre": "Usuario",
    "puerto": "COM3" if os.name == "nt" else "",  # vacío = autodetectar
    "intervalo": 5,
    "demo": False,
    "autoiniciar": True,
}

TIPOS_AVATAR = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
RE_SID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
RE_PUERTO = re.compile(r"^[A-Za-z0-9_./:@ ][A-Za-z0-9_./:@ \-]{0,59}$")


def asegurar_carpetas():
    for d in (DATA, DIR_SCANS, DIR_TXT, DIR_CSV, DIR_AVATAR):
        os.makedirs(d, exist_ok=True)


# ---------- Dependencias (se instalan solas) ----------

def estado_dependencias():
    return {pkg: importlib.util.find_spec(mod) is not None for mod, pkg in DEPENDENCIAS}


def instalar_dependencias(forzar=False):
    """Instala pyserial y mpremote con pip si faltan. Devuelve (ok, texto_log)."""
    faltan = [pkg for mod, pkg in DEPENDENCIAS if forzar or importlib.util.find_spec(mod) is None]
    if not faltan:
        return True, "Todo está instalado: pyserial y mpremote."

    base_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *faltan]
    try:
        r = subprocess.run(base_cmd, capture_output=True, text=True, timeout=300, creationflags=NO_WINDOW)
        salida = (r.stdout or "") + (r.stderr or "")
        # Linux con Python "externally managed": reintento permitido
        if r.returncode != 0 and "externally-managed" in salida:
            r = subprocess.run(base_cmd + ["--break-system-packages"], capture_output=True, text=True,
                               timeout=300, creationflags=NO_WINDOW)
            salida = (r.stdout or "") + (r.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"No se pudo ejecutar pip: {e}"

    importlib.invalidate_caches()
    ok = r.returncode == 0 and all(estado_dependencias().values())
    cola = "\n".join(salida.strip().splitlines()[-12:])
    return ok, (f"Instalado: {', '.join(faltan)}\n" if ok else "Falló la instalación.\n") + cola


# ---------- Fabricantes (offline) ----------

def _cargar_tabla_oui():
    if not os.path.exists(OUI_LOCAL):
        print(f"[aviso] No se encontró {OUI_LOCAL}. Los fabricantes saldrán como 'Desconocido'.")
        return {}
    try:
        with open(OUI_LOCAL, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"[aviso] No se pudo leer {OUI_LOCAL}; ¿está corrupto?")
        return {}


_TABLA_OUI = _cargar_tabla_oui()


def buscar_fabricante(bssid):
    oui = bssid.upper()[0:8]
    if oui in FABRICANTES_LOCALES:
        return FABRICANTES_LOCALES[oui]
    if oui in _TABLA_OUI:
        return _TABLA_OUI[oui]
    # Bit "administrada localmente": redes invitadas/secundarias o MAC aleatorias, no tienen OUI público
    try:
        if int(bssid[0:2], 16) & 0x02:
            return "MAC local (red secundaria / aleatoria)"
    except ValueError:
        pass
    return "Desconocido"


# ---------- Utilidades de señal ----------

def formatear_mac(bssid_bytes):
    return ":".join(f"{b:02x}" for b in bssid_bytes)


def calidad_senal(rssi):
    if rssi >= -50:
        return 100, "Excelente"
    if rssi >= -60:
        return 80, "Muy buena"
    if rssi >= -70:
        return 60, "Buena"
    if rssi >= -80:
        return 40, "Débil"
    return 20, "Muy débil"


def estimar_distancia(rssi, rssi_1m=-63.4, n=2.0):
    return round(10 ** ((rssi_1m - rssi) / (10 * n)), 2)


def armar_red(ssid, bssid, canal, rssi, seguridad, oculta):
    calidad, etiqueta = calidad_senal(rssi)
    return {
        "ssid": ssid or "(oculta / sin nombre)",
        "bssid": bssid,
        "fabricante": buscar_fabricante(bssid),
        "canal": canal,
        "banda": "2.4 GHz" if canal <= 14 else "5 GHz",
        "rssi": rssi,
        "calidad_pct": calidad,
        "calidad_txt": etiqueta,
        "distancia_aprox_m": estimar_distancia(rssi),
        "seguridad": SEGURIDAD.get(seguridad, f"Desconocida ({seguridad})"),
        "oculta": "Sí" if oculta else "No",
    }


# ---------- Configuración ----------

_cfg_lock = threading.Lock()


def leer_config():
    cfg = dict(CONFIG_DEFECTO)
    with _cfg_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                guardada = json.load(f)
            if isinstance(guardada, dict):
                cfg.update({k: v for k, v in guardada.items() if k in CONFIG_DEFECTO})
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def guardar_config(cambios):
    """Valida y guarda. Lanza ValueError si algo no es válido."""
    cfg = leer_config()
    if "nombre" in cambios:
        nombre = str(cambios["nombre"]).strip()[:40]
        cfg["nombre"] = nombre or "Usuario"
    if "puerto" in cambios:
        puerto = str(cambios["puerto"]).strip()
        if puerto and not RE_PUERTO.match(puerto):
            raise ValueError("El puerto contiene caracteres no válidos.")
        cfg["puerto"] = puerto
    if "intervalo" in cambios:
        try:
            cfg["intervalo"] = max(2, min(300, int(cambios["intervalo"])))
        except (TypeError, ValueError):
            raise ValueError("El intervalo debe ser un número entre 2 y 300.")
    for clave in ("demo", "autoiniciar"):
        if clave in cambios:
            cfg[clave] = bool(cambios[clave])
    with _cfg_lock:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
    return cfg


def ruta_avatar():
    try:
        for nombre in os.listdir(DIR_AVATAR):
            if nombre.startswith("perfil."):
                return os.path.join(DIR_AVATAR, nombre)
    except OSError:
        pass
    return None


def config_publica():
    cfg = leer_config()
    ruta = ruta_avatar()
    cfg["avatar"] = f"/avatar?v={int(os.path.getmtime(ruta))}" if ruta else ""
    return cfg


# ---------- Puertos y placa ----------

def listar_puertos():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    salida = []
    for p in list_ports.comports():
        salida.append({
            "device": p.device,
            "desc": p.description or "",
            "probable": p.vid in VIDS_ESP,
        })
    salida.sort(key=lambda x: (not x["probable"], x["device"]))
    return salida


class ErrorPlaca(Exception):
    pass


def traducir_error(texto):
    t = (texto or "").lower()
    if "no module named mpremote" in t:
        return "mpremote no está instalado. Pulsa «Instalar / reparar» en Ajustes."
    if "access is denied" in t or "permissionerror" in t or "busy" in t:
        return "Puerto ocupado: cierra Thonny, Arduino IDE u otros programas que lo estén usando."
    if "could not open port" in t or "no such file" in t or "filenotfound" in t:
        return "No se encuentra el puerto. Revisa el cable USB y el puerto en Ajustes."
    if "failed to access" in t:
        return "No se puede acceder al puerto (¿ocupado o desconectado?)."
    if "timed out" in t or "timeout" in t:
        return "La placa no responde (tiempo agotado)."
    lineas = [l for l in (texto or "").strip().splitlines() if l.strip()]
    return lineas[-1][:200] if lineas else "La placa no respondió."


def _mpremote(puerto, codigo, timeout):
    cmd = [sys.executable, "-m", "mpremote", "connect", puerto, "exec", codigo]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def resolver_puerto(cfg):
    if cfg["puerto"]:
        return cfg["puerto"]
    for p in listar_puertos():
        if p["probable"]:
            return p["device"]
    raise ErrorPlaca("No se detecta ninguna ESP32. Conéctala por USB o indica el puerto en Ajustes.")


DEMO_REDES = [
    ("MiFibra-5G", "e4:3e:d7:11:22:33", 36, -48, 3),
    ("MiFibra-2G", "e4:3e:d7:11:22:31", 6, -52, 3),
    ("MOVISTAR_8F2A", "f8:0b:be:5a:10:22", 1, -61, 4),
    ("vodafone4B10", "ac:9b:0a:77:aa:10", 11, -67, 3),
    ("Cafeteria_Libre", "b0:be:76:01:02:03", 6, -73, 0),
    ("DIRECT-roku-34", "da:a1:19:44:55:66", 11, -79, 6),
    ("(oculta)", "00:1a:2b:3c:4d:5e", 9, -84, 3),
    ("Orange-C1D9", "7c:10:c9:12:34:56", 44, -71, 7),
]


def escaneo_redes(cfg):
    if cfg["demo"]:
        time.sleep(0.6)
        redes = []
        for ssid, bssid, canal, base, seg in DEMO_REDES:
            if random.random() < 0.08:
                continue
            redes.append(armar_red("" if ssid == "(oculta)" else ssid, bssid, canal,
                                   base + random.randint(-4, 4), seg, ssid == "(oculta)"))
        return redes

    puerto = resolver_puerto(cfg)
    try:
        r = _mpremote(puerto, COD_ESCANEO, timeout=25)
    except subprocess.TimeoutExpired:
        raise ErrorPlaca("La placa no responde (tiempo agotado).")
    except OSError as e:
        raise ErrorPlaca(f"No se pudo lanzar mpremote: {e}")

    if r.returncode != 0:
        raise ErrorPlaca(traducir_error((r.stderr or "") + (r.stdout or "")))

    linea = next((l for l in reversed((r.stdout or "").strip().splitlines()) if l.startswith("[")), None)
    if linea is None:
        raise ErrorPlaca("Respuesta inesperada de la placa.")
    try:
        bruto = ast.literal_eval(linea)
    except (ValueError, SyntaxError):
        raise ErrorPlaca("No se pudo interpretar la respuesta de la placa.")

    redes = []
    for ssid, bssid, canal, rssi, seguridad, oculta in bruto:
        redes.append(armar_red(ssid.decode("utf-8", errors="ignore"), formatear_mac(bssid),
                               canal, rssi, seguridad, oculta))
    return redes


# ---------- Almacenamiento (todo dentro de data/) ----------

_lock = threading.RLock()  # protege estado + ficheros
CAB_CSV = ["Timestamp", "SSID", "BSSID", "Fabricante", "Canal", "Banda", "RSSI",
           "Calidad", "Distancia_m", "Seguridad", "Oculta"]


def _ruta(sid, tipo):
    if tipo == "jsonl":
        return os.path.join(DIR_SCANS, f"{sid}.jsonl")
    if tipo == "txt":
        return os.path.join(DIR_TXT, f"{sid} sesion.txt")
    return os.path.join(DIR_CSV, f"{sid} sesion.csv")


def _txt_registro(f, reg):
    f.write(f"\n--- Escaneo {reg['ts']} ---\n")
    for r in reg["redes"]:
        f.write(f"{r['ssid']} | {r['bssid']} | {r['fabricante']} | canal {r['canal']} | "
                f"{r['rssi']} dBm | {r['calidad_txt']} | ~{r['distancia_aprox_m']} m | "
                f"{r['seguridad']} | oculta: {r['oculta']}\n")


def _csv_registro(w, reg):
    for r in reg["redes"]:
        w.writerow([reg["ts"], r["ssid"], r["bssid"], r["fabricante"], r["canal"], r["banda"], r["rssi"],
                    r["calidad_txt"], r["distancia_aprox_m"], r["seguridad"], r["oculta"]])


def leer_registros(sid):
    ruta = _ruta(sid, "jsonl")
    if not os.path.exists(ruta):
        return None
    regs = []
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea:
                try:
                    regs.append(json.loads(linea))
                except json.JSONDecodeError:
                    pass
    return regs


def guardar_escaneo(redes):
    with _lock:
        sid = estado["sesion"]
        if not sid:
            sid = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            estado["sesion"] = sid
            estado["n_escaneos"] = 0
        reg = {"id": int(time.time() * 1000), "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "redes": redes}
        with open(_ruta(sid, "jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        with open(_ruta(sid, "txt"), "a", encoding="utf-8") as f:
            _txt_registro(f, reg)
        ruta_csv = _ruta(sid, "csv")
        nuevo = not os.path.exists(ruta_csv)
        with open(ruta_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(CAB_CSV)
            _csv_registro(w, reg)
        estado["n_escaneos"] += 1
        return reg


def regenerar_logs(sid, regs):
    with open(_ruta(sid, "txt"), "w", encoding="utf-8") as f:
        for reg in regs:
            _txt_registro(f, reg)
    with open(_ruta(sid, "csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CAB_CSV)
        for reg in regs:
            _csv_registro(w, reg)


def listar_sesiones():
    sesiones = []
    with _lock:
        try:
            nombres = sorted(os.listdir(DIR_SCANS), reverse=True)
        except OSError:
            nombres = []
        for nombre in nombres:
            if not nombre.endswith(".jsonl"):
                continue
            sid = nombre[:-6]
            if not RE_SID.match(sid):
                continue
            regs = leer_registros(sid) or []
            unicas = {r["bssid"] for reg in regs for r in reg["redes"]}
            sesiones.append({
                "id": sid,
                "inicio": regs[0]["ts"] if regs else sid,
                "fin": regs[-1]["ts"] if regs else sid,
                "escaneos": len(regs),
                "redes_unicas": len(unicas),
                "activa": sid == estado["sesion"] and estado["escaneando"],
            })
    return sesiones


def borrar_sesion(sid):
    borrado = False
    with _lock:
        for tipo in ("jsonl", "txt", "csv"):
            try:
                os.remove(_ruta(sid, tipo))
                borrado = True
            except FileNotFoundError:
                pass
        if estado["sesion"] == sid:
            estado["sesion"] = None
            estado["n_escaneos"] = 0
    return borrado


def borrar_escaneo(sid, reg_id):
    with _lock:
        regs = leer_registros(sid)
        if regs is None:
            return False
        restantes = [r for r in regs if r.get("id") != reg_id]
        if len(restantes) == len(regs):
            return False
        if not restantes:
            return borrar_sesion(sid)
        with open(_ruta(sid, "jsonl"), "w", encoding="utf-8") as f:
            for r in restantes:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        regenerar_logs(sid, restantes)
        if estado["sesion"] == sid:
            estado["n_escaneos"] = len(restantes)
        return True


def borrar_todo():
    n = 0
    with _lock:
        for s in listar_sesiones():
            if borrar_sesion(s["id"]):
                n += 1
    return n


# ---------- Estado en vivo + difusión (SSE) ----------

estado = {
    "escaneando": False,   # el bucle automático está activo
    "midiendo": False,     # hay un escaneo en curso ahora mismo
    "conectado": False,
    "mensaje": "Detenido",
    "error": "",
    "ultimo": None,
    "sesion": None,
    "n_escaneos": 0,
}
ultimas_redes = []

_cond = threading.Condition()
_version = 0


def publicar():
    global _version
    with _cond:
        _version += 1
        _cond.notify_all()


def snapshot():
    with _lock:
        return {
            "config": config_publica(),
            "estado": dict(estado),
            "redes": sorted(ultimas_redes, key=lambda r: r["rssi"], reverse=True),
            "deps": estado_dependencias(),
        }


# ---------- Bucle de escaneo ----------

_placa_lock = threading.Lock()  # el puerto serie solo admite un cliente a la vez


class Escaner:
    def __init__(self):
        self.hilo = None
        self.parar = threading.Event()
        self.despertar = threading.Event()

    def iniciar(self):
        if self.hilo and self.hilo.is_alive():
            return
        self.parar.clear()
        self.despertar.clear()
        with _lock:
            estado.update(escaneando=True, mensaje="Iniciando…", error="")
            estado["sesion"] = None  # cada arranque = sesión nueva
            estado["n_escaneos"] = 0
        publicar()
        self.hilo = threading.Thread(target=self._bucle, daemon=True)
        self.hilo.start()

    def detener(self):
        self.parar.set()
        self.despertar.set()
        with _lock:
            estado.update(escaneando=False, midiendo=False, mensaje="Detenido")
        publicar()

    def ahora(self):
        if self.hilo and self.hilo.is_alive():
            self.despertar.set()

    def _bucle(self):
        while not self.parar.is_set():
            self._ciclo()
            intervalo = leer_config()["intervalo"]
            self.despertar.wait(timeout=intervalo)
            self.despertar.clear()

    def _ciclo(self):
        cfg = leer_config()
        with _lock:
            estado.update(midiendo=True, mensaje="Escaneando…")
        publicar()
        try:
            with _placa_lock:
                redes = escaneo_redes(cfg)
        except ErrorPlaca as e:
            if not self.parar.is_set():
                with _lock:
                    estado.update(midiendo=False, conectado=False, error=str(e), mensaje="Sin placa")
                publicar()
            return
        except Exception as e:  # nunca matar el hilo por un fallo inesperado
            with _lock:
                estado.update(midiendo=False, conectado=False, error=f"Error inesperado: {e}", mensaje="Error")
            publicar()
            return

        if self.parar.is_set():
            return
        if redes:
            guardar_escaneo(redes)
        with _lock:
            ultimas_redes[:] = redes
            estado.update(midiendo=False, conectado=True, error="", mensaje="Conectado",
                          ultimo=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        publicar()


escaner = Escaner()


def probar_conexion():
    cfg = leer_config()
    if cfg["demo"]:
        return True, "Modo demo activo: no se usa la placa."
    if not _placa_lock.acquire(timeout=30):
        return False, "La placa está ocupada escaneando; vuelve a probar en unos segundos."
    try:
        puerto = resolver_puerto(cfg)
        r = _mpremote(puerto, "print('ok')", timeout=10)
        if r.returncode == 0 and "ok" in (r.stdout or ""):
            return True, f"Placa detectada correctamente en {puerto}."
        return False, traducir_error((r.stderr or "") + (r.stdout or ""))
    except ErrorPlaca as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, "La placa no responde (tiempo agotado)."
    except OSError as e:
        return False, f"No se pudo lanzar mpremote: {e}"
    finally:
        _placa_lock.release()


# ---------- Servidor HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Scanpa/2.0"

    def log_message(self, *args):  # silencio en consola
        pass

    # --- helpers ---
    def _enviar(self, codigo, cuerpo, tipo="application/json; charset=utf-8", extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def json(self, datos, codigo=200):
        self._enviar(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"))

    def error(self, codigo, mensaje):
        self.json({"error": mensaje}, codigo)

    def _cuerpo(self, maximo=5 * 1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > maximo:
            raise ValueError("Archivo demasiado grande.")
        return self.rfile.read(n) if n else b""

    def _json_entrada(self):
        try:
            datos = json.loads(self._cuerpo() or b"{}")
            return datos if isinstance(datos, dict) else {}
        except json.JSONDecodeError:
            raise ValueError("JSON no válido.")

    def _fichero(self, ruta, tipo=None, descarga=None):
        try:
            with open(ruta, "rb") as f:
                cuerpo = f.read()
        except OSError:
            return self.error(404, "No encontrado")
        tipo = tipo or mimetypes.guess_type(ruta)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript", "application/json"):
            tipo += "; charset=utf-8"
        extra = {"Content-Disposition": f'attachment; filename="{descarga}"'} if descarga else None
        self._enviar(200, cuerpo, tipo, extra)

    # --- GET ---
    def do_GET(self):
        url = urlparse(self.path)
        p = unquote(url.path)

        if p == "/api/estado":
            return self.json(snapshot())
        if p == "/api/eventos":
            return self._sse()
        if p == "/api/puertos":
            return self.json({"puertos": listar_puertos()})
        if p == "/api/sesiones":
            return self.json({"sesiones": listar_sesiones()})

        m = re.fullmatch(r"/api/sesiones/([\d_-]+)", p)
        if m and RE_SID.match(m.group(1)):
            with _lock:
                regs = leer_registros(m.group(1))
            if regs is None:
                return self.error(404, "Sesión no encontrada")
            return self.json({"id": m.group(1), "escaneos": regs})

        m = re.fullmatch(r"/api/sesiones/([\d_-]+)/descargar", p)
        if m and RE_SID.match(m.group(1)):
            formato = (parse_qs(url.query).get("formato") or ["csv"])[0]
            formato = "txt" if formato == "txt" else "csv"
            ruta = _ruta(m.group(1), formato)
            if not os.path.exists(ruta):
                return self.error(404, "Sesión no encontrada")
            return self._fichero(ruta, "text/plain" if formato == "txt" else "text/csv",
                                 f"{m.group(1)}_sesion.{formato}")

        if p == "/avatar":
            ruta = ruta_avatar()
            return self._fichero(ruta) if ruta else self.error(404, "Sin avatar")

        # Ficheros estáticos de la interfaz (lista blanca de extensiones)
        if p == "/":
            p = "/index.html"
        ruta = os.path.realpath(os.path.join(WEB, p.lstrip("/")))
        ext = os.path.splitext(ruta)[1].lower()
        if (os.path.dirname(ruta) != os.path.realpath(WEB) and not ruta.startswith(os.path.realpath(WEB) + os.sep)) \
                or ext not in EXT_WEB or not os.path.isfile(ruta):
            return self.error(404, "No encontrado")
        self._fichero(ruta, EXT_WEB[ext])

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        visto = -1
        try:
            while True:
                with _cond:
                    if _version == visto:
                        _cond.wait(15)
                    v = _version
                if v != visto:
                    visto = v
                    self.wfile.write(f"data: {json.dumps(snapshot(), ensure_ascii=False)}\n\n".encode("utf-8"))
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    # --- POST ---
    def do_POST(self):
        p = unquote(urlparse(self.path).path)
        try:
            if p == "/api/config":
                guardar_config(self._json_entrada())
                publicar()
                return self.json({"ok": True, "config": config_publica()})

            if p == "/api/avatar":
                tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if tipo not in TIPOS_AVATAR:
                    return self.error(400, "Formato no admitido (usa JPG, PNG, WEBP o GIF).")
                cuerpo = self._cuerpo(3 * 1024 * 1024)
                if not cuerpo:
                    return self.error(400, "Archivo vacío.")
                asegurar_carpetas()
                for n in os.listdir(DIR_AVATAR):
                    if n.startswith("perfil."):
                        os.remove(os.path.join(DIR_AVATAR, n))
                with open(os.path.join(DIR_AVATAR, f"perfil.{TIPOS_AVATAR[tipo]}"), "wb") as f:
                    f.write(cuerpo)
                publicar()
                return self.json({"ok": True, "config": config_publica()})

            if p == "/api/escaneo/iniciar":
                escaner.iniciar()
                return self.json({"ok": True})
            if p == "/api/escaneo/detener":
                escaner.detener()
                return self.json({"ok": True})
            if p == "/api/escaneo/ahora":
                if not estado["escaneando"]:
                    escaner.iniciar()
                else:
                    escaner.ahora()
                return self.json({"ok": True})

            if p == "/api/instalar":
                ok, log = instalar_dependencias(forzar=bool(self._json_entrada().get("forzar")))
                publicar()
                return self.json({"ok": ok, "log": log})

            if p == "/api/probar":
                ok, mensaje = probar_conexion()
                return self.json({"ok": ok, "mensaje": mensaje})
        except ValueError as e:
            return self.error(400, str(e))
        except OSError as e:
            return self.error(500, f"Error de disco: {e}")
        self.error(404, "No encontrado")

    # --- DELETE ---
    def do_DELETE(self):
        p = unquote(urlparse(self.path).path)

        if p == "/api/avatar":
            for n in os.listdir(DIR_AVATAR):
                if n.startswith("perfil."):
                    os.remove(os.path.join(DIR_AVATAR, n))
            publicar()
            return self.json({"ok": True, "config": config_publica()})

        if p == "/api/sesiones":
            n = borrar_todo()
            publicar()
            return self.json({"ok": True, "borradas": n})

        m = re.fullmatch(r"/api/sesiones/([\d_-]+)", p)
        if m and RE_SID.match(m.group(1)):
            ok = borrar_sesion(m.group(1))
            publicar()
            return self.json({"ok": ok}) if ok else self.error(404, "Sesión no encontrada")

        m = re.fullmatch(r"/api/sesiones/([\d_-]+)/escaneos/(\d+)", p)
        if m and RE_SID.match(m.group(1)):
            ok = borrar_escaneo(m.group(1), int(m.group(2)))
            publicar()
            return self.json({"ok": ok}) if ok else self.error(404, "Registro no encontrado")

        self.error(404, "No encontrado")


# ---------- Arranque ----------

def main():
    # Consola de Windows: evita errores con tildes y símbolos
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    asegurar_carpetas()
    if not os.path.isfile(os.path.join(WEB, "index.html")):
        print(f"[error] No encuentro index.html en {WEB}. Deja index.html, style.css y app.js junto a scanpa.py.")
        sys.exit(1)
    print("=" * 56)
    print("  SCANPA · escáner WiFi con ESP32")
    print("=" * 56)

    print("Comprobando dependencias (pyserial, mpremote)...")
    ok, log = instalar_dependencias()
    print(("[ok] " if ok else "[error] ") + log.splitlines()[0])
    if not ok:
        print("       Podrás reintentarlo desde Ajustes > Instalar / reparar.")

    if leer_config()["autoiniciar"]:
        escaner.iniciar()

    servidor = None
    for puerto in range(PUERTO_WEB, PUERTO_WEB + 10):
        try:
            servidor = ThreadingHTTPServer((HOST, puerto), Handler)
            break
        except OSError:
            continue
    if servidor is None:
        print("No se pudo abrir ningún puerto local libre.")
        sys.exit(1)
    servidor.daemon_threads = True

    url = f"http://{'127.0.0.1' if HOST in ('0.0.0.0', '') else HOST}:{servidor.server_address[1]}"
    print(f"\nInterfaz lista en  {url}\n(Ctrl+C para salir)\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando...")
    finally:
        escaner.detener()
        servidor.server_close()


if __name__ == "__main__":
    main()