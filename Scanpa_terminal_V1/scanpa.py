import subprocess   # Para lanzar 'mpremote' (el conector con la placa) como proceso externo
import ast           # Para convertir el texto que devuelve la placa en una lista/tupla Python real
import sys
import os
import json
import csv
import time           # Para el bucle de refresco automático
from datetime import datetime  # Para poner fecha y hora en el nombre del fichero de log


# ---------- Configuración ----------

PUERTO = "COM3"  # Puerto serie donde está conectada la placa MicroPython

OUI_LOCAL = "oui_local.json"  # Fichero con la tabla de fabricantes por prefijo MAC (OUI)


INTERVALO_SEGUNDOS = 5  # Cada cuántos segundos se repite el escaneo automáticamente

# Arte ASCII que se imprime al arrancar. Puramente estético, no afecta a la lógica.
TITULO = r"""
╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                                                     ║
║                                                                                                                                     ║
║                                         ███████╗ ██████╗ █████╗ ███╗   ██╗ ██████╗   █████╗                                         ║
║                                         ██╔════╝██╔════╝██╔══██╗████╗  ██║ ██╔══██╗ ██╔══██╗                                        ║
║                                         ███████╗██║     ███████║██╔██╗ ██║ ██████╔╝ ███████║                                        ║
║                                         ╚════██║██║     ██╔══██╗██║╚██╗██║ ██╔═══╝  ██╔══██║                                        ║
║                                         ███████║╚██████╗██║  ██║██║ ╚████║ ██║      ██║  ██║                                        ║
║                                         ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═╝      ╚═╝  ╚═╝                                        ║
║                                                                                                                                     ║
║                                                                                                                                     ║
║                                    📡   e s c a n e a n d o   p u n t o s   d e   a c c e s o   📡                                  ║
║                                                                                                                                     ║
║                                             ────────────────────────────────────────────                                            ║
║                                              w i f i   ·   n e t w o r k   ·   s c a n                                              ║
║                                                                                                                                     ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
"""

# Mapeo de los códigos de seguridad que devuelve MicroPython en sta.scan().
# El .get() más abajo usa un valor por defecto, así que un código nuevo
# que no esté aquí no rompe el script: simplemente se muestra como "Desconocida (n)".
SEGURIDAD = {
    0: "Abierta (sin contraseña)",
    1: "WEP",
    2: "WPA-PSK",
    3: "WPA2-PSK",
    4: "WPA/WPA2-PSK",
    5: "WPA2-Enterprise",
    6: "WPA3-PSK",
    7: "WPA2/WPA3-PSK"
}

# Tabla de arranque: rellénala a mano con los fabricantes/routers que
# reconozcas en tu entorno (operadoras españolas, vecinos, etc.).
# Formato: "AA:BB:CC" (mayúsculas, con dos puntos) -> nombre a mostrar.
# Tiene prioridad sobre la consulta a la API.
FABRICANTES_LOCALES = {
    # "F8:0B:BE": "Movistar (router HGU)",
    # "AC:9B:0A": "Vodafone (router)",
}


# ---------- Resolución de fabricante por OUI (100% offline) ----------

def _cargar_tabla_oui():
    """
    Carga oui_local.json, generado una única vez con 'descargar_oui.py'.
    Si no existe, avisa y sigue funcionando solo con FABRICANTES_LOCALES.
    """
    if os.path.exists(OUI_LOCAL):
        try:
            with open(OUI_LOCAL, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # Fichero presente pero ilegible (corrupto o permisos): seguimos sin
            # tabla en vez de reventar el programa entero por esto.
            print(f"⚠️  No se pudo leer {OUI_LOCAL}, ¿está corrupto? Vuelve a ejecutar descargar_oui.py")
            return {}
    else:
        print(f"⚠️  No se encontró {OUI_LOCAL}. Ejecuta 'python descargar_oui.py' una vez (necesita internet) "
              f"para tener la tabla completa de fabricantes en local.")
        return {}


# Se ejecuta UNA sola vez, al importar el módulo (no dentro de una función),
# así el JSON se carga en memoria una vez y todas las llamadas a
# buscar_fabricante() reutilizan el mismo diccionario sin volver a leer disco.
_TABLA_OUI = _cargar_tabla_oui()


def buscar_fabricante(bssid):
    """
    bssid en formato 'aa:bb:cc:dd:ee:ff'. Devuelve el nombre del fabricante.
    No hace ninguna consulta a internet: todo sale de ficheros locales.
    Orden de prioridad: tabla local manual -> tabla OUI descargada -> "Desconocido".
    """
    # Los primeros 3 bytes de la MAC (6 hex, 8 caracteres con los dos-puntos)
    # son el OUI: el identificador de fabricante asignado por el IEEE.
    oui = bssid.upper()[0:8]  # "AA:BB:CC"

    if oui in FABRICANTES_LOCALES:
        return FABRICANTES_LOCALES[oui]

    return _TABLA_OUI.get(oui, "Desconocido")


# ---------- Resto del script ----------

def formatear_mac(bssid_bytes):
    # La placa devuelve el BSSID como bytes (ej. b'\x5c\xe9\x31...').
    # Esto lo convierte a texto tipo "5c:e9:31:aa:bb:cc" en minúsculas.
    return ':'.join(f'{b:02x}' for b in bssid_bytes)


def calidad_señal(rssi):
    """Convierte RSSI (dBm) a un % aproximado de calidad, y a una etiqueta"""
    # Escala heurística por umbrales fijos, no un estándar oficial:
    # cuanto menos negativo el dBm, mejor señal.
    if rssi >= -50:
        calidad = 100
        etiqueta = "Excelente"
    elif rssi >= -60:
        calidad = 80
        etiqueta = "Muy buena"
    elif rssi >= -70:
        calidad = 60
        etiqueta = "Buena"
    elif rssi >= -80:
        calidad = 40
        etiqueta = "Débil"
    else:
        calidad = 20
        etiqueta = "Muy débil"
    return calidad, etiqueta


def estimar_distancia(rssi, rssi_1m=-63.4, n=2.0):
    # Modelo clásico de pérdida de trayecto logarítmico:
    #   d = 10 ^ ((RSSI_1m - RSSI) / (10 * n))
    # Con un único punto de calibración esto da una estimación gruesa
    # (sirve para comparar "qué red está más cerca", no para medir metros exactos).
    return round(10 ** ((rssi_1m - rssi) / (10 * n)), 2)


def placa_conectada(puerto=PUERTO):
    """Comprueba si la placa MicroPython responde en el puerto indicado."""
    try:
        # Lanza mpremote como subproceso y le pide ejecutar print('ok') en la placa.
        # Si responde "ok" y termina sin error, consideramos la placa conectada.
        resultado = subprocess.run(
            ["python", "-m", "mpremote", "connect", puerto, "exec", "print('ok')"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return resultado.returncode == 0 and "ok" in resultado.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Timeout, mpremote no instalado, o fallo del propio puerto USB:
        # en cualquier caso, tratamos como "no conectada" en vez de reventar.
        return False


def esperar_placa():
    """Bloquea hasta que la placa esté conectada, avisando al usuario."""
    # Bucle simple de "reintenta hasta que funcione": pide Enter y vuelve a comprobar.
    while not placa_conectada():
        print(f"\n❌ ERROR: No se ha detectado ninguna placa en el puerto {PUERTO}.")
        print("   Conecta la placa por USB y pulsa Enter para reintentar (o Ctrl+C para salir)...")
        input()
    print(f"\n✅ Placa detectada correctamente en {PUERTO}. Iniciando escaneo...\n")


def escaneo_redes():
    # Ejecuta código MicroPython DENTRO de la placa vía "mpremote exec":
    # activa el WiFi en modo estación y llama a sta.scan(), que devuelve
    # una lista de tuplas (ssid, bssid, canal, rssi, seguridad, oculta).
    # El print() de la placa es lo que llega aquí como stdout del subprocess.
    resultado = subprocess.run(
        [
            "python", "-m", "mpremote", "connect", PUERTO, "exec",
            "import network; sta = network.WLAN(network.STA_IF); sta.active(True); print(sta.scan())"
        ],
        capture_output=True,
        text=True
    )

    if resultado.stderr:
        # No se lanza excepción: se avisa y se devuelve lista vacía para que
        # el resto del programa siga funcionando (mostrará "No se detectaron redes").
        print("Errores: ", resultado.stderr)
        return []

    # El stdout es texto con pinta de lista Python, ej:
    # [(b'MiWifi', b'\x5c\xe9\x31...', 6, -55, 3, 0), ...]
    # ast.literal_eval lo convierte de forma SEGURA en objetos Python reales
    # (a diferencia de eval, solo permite literales, no código arbitrario).
    redes_raw = ast.literal_eval(resultado.stdout.strip())
    redes = []

    for ssid, bssid, canal, rssi, seguridad, oculta in redes_raw:
        calidad, etiqueta = calidad_señal(rssi)
        bssid_fmt = formatear_mac(bssid)
        redes.append({
            "ssid": ssid.decode("utf-8", errors="ignore") or "(oculta / sin nombre)",
            "bssid": bssid_fmt,
            "fabricante": buscar_fabricante(bssid_fmt),
            "canal": canal,
            "banda": "2.4 GHz" if canal <= 14 else "5 GHz",  # Los canales 1-14 son banda 2.4 GHz
            "rssi": rssi,
            "calidad_pct": calidad,
            "calidad_txt": etiqueta,
            "distancia_aprox_m": estimar_distancia(rssi),
            "seguridad": SEGURIDAD.get(seguridad, f"Desconocida ({seguridad})"),
            "oculta": "Sí" if oculta else "No"
        })

    return redes


# ---------- Logging acumulativo (una sola sesión = un solo fichero) ----------
# A diferencia de la versión original (que creaba un fichero nuevo por cada
# escaneo), ahora se abre UN fichero TXT y UN fichero CSV al arrancar el
# programa, con timestamp en el nombre, y cada vuelta del bucle les AÑADE
# (append) las filas del nuevo escaneo con su propia marca de tiempo.
#
# NOVEDAD: además, se lleva un set() con los BSSID que ya se han escrito
# alguna vez en el registro durante esta sesión. Antes de escribir, se
# descartan los puntos de acceso que ya estén en ese set, así cada AP
# aparece UNA ÚNICA VEZ en los ficheros de log, aunque el escaneo en
# pantalla lo siga mostrando en cada vuelta mientras esté al alcance.

def preparar_ficheros_log():
    """Crea las carpetas y decide el nombre (fijo para toda la sesión) de los logs."""
    carpeta_txt = "log_txt"
    carpeta_csv = "log_csv"
    os.makedirs(carpeta_txt, exist_ok=True)
    os.makedirs(carpeta_csv, exist_ok=True)

    sello = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta_txt = os.path.join(carpeta_txt, f"{sello} sesion.txt")
    ruta_csv = os.path.join(carpeta_csv, f"{sello} sesion.csv")

    # Cabecera del CSV, una sola vez
    with open(ruta_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "SSID", "BSSID", "Fabricante", "Canal", "RSSI",
                          "Calidad", "Distancia_m", "Seguridad", "Oculta"])

    # El TXT no necesita cabecera, se abre vacío y ya
    open(ruta_txt, "w", encoding="utf-8").close()

    return ruta_txt, ruta_csv


def filtrar_nuevas(redes, bssids_registrados):
    """
    Devuelve solo los puntos de acceso cuyo BSSID todavía NO se ha
    registrado en esta sesión. También actualiza el set in-place,
    marcando esos BSSID como ya vistos.
    """
    nuevas = []
    for red in redes:
        if red["bssid"] not in bssids_registrados:
            nuevas.append(red)
            bssids_registrados.add(red["bssid"])
    return nuevas


def registrar_redes_txt(redes, ruta_txt):
    if not redes:  # Nada nuevo que registrar: no escribimos ni la marca de tiempo
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ruta_txt, "a", encoding="utf-8") as file:
        file.write(f"\n--- Nuevos puntos de acceso detectados {timestamp} ---\n")
        for red in redes:
            file.write(
                f"{red['ssid']} | {red['bssid']} | {red['fabricante']} | canal {red['canal']} | "
                f"{red['rssi']} dBm | {red['calidad_txt']} | ~{red['distancia_aprox_m']} m | "
                f"{red['seguridad']} | oculta: {red['oculta']}\n"
            )


def registrar_redes_csv(redes, ruta_csv):
    if not redes:  # Nada nuevo que registrar
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ruta_csv, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for red in redes:
            writer.writerow([
                timestamp,
                red["ssid"],
                red["bssid"],
                red["fabricante"],
                red["canal"],
                red["rssi"],
                red["calidad_txt"],
                red["distancia_aprox_m"],
                red["seguridad"],
                red["oculta"],
            ])


def limpiar_pantalla():
    # 'cls' en Windows, 'clear' en Linux/Mac
    os.system("cls" if os.name == "nt" else "clear")


def mostrar_resultados(redes, ultima_actualizacion, total_registradas):
    limpiar_pantalla()
    print(TITULO)
    print("-" * 135)
    print(f"\n Se han detectado {len(redes)} redes en este escaneo "
          f"({total_registradas} puntos de acceso únicos registrados hasta ahora).\n")
    print("-" * 135)

    if not redes:
        print("No se detectaron redes.")
        return

    # Ordena por RSSI descendente: la señal más potente (menos negativa) primero
    redes_ordenadas = sorted(redes, key=lambda r: r["rssi"], reverse=True)

    # {:<N} = alineación a la izquierda con ancho fijo, para que la tabla quede en columnas
    print(f"{'SSID':<20} {'BSSID':<18} {'Fabricante':<22} {'Canal':<7} {'RSSI':<7} {'Calidad':<12} {'Dist. (m)':<10} {'Seguridad':<18} {'Oculta'}")
    print("-" * 135)

    for red in redes_ordenadas:
        print(f"{red['ssid']:<20} {red['bssid']:<18} {red['fabricante']:<22} {red['canal']:<7} "
              f"{red['rssi']:<7} {red['calidad_txt']:<12} {red['distancia_aprox_m']:<10} "
              f"{red['seguridad']:<18} {red['oculta']}")

    mejor = redes_ordenadas[0]  # Primera tras ordenar = la de señal más fuerte
    print("-" * 135)
    print(f"\nRed más potente: {mejor['ssid']} | {mejor['fabricante']} | {mejor['rssi']} dBm | "
          f"Calidad: {mejor['calidad_txt']} ({mejor['calidad_pct']}%) | "
          f"Distancia aprox.: {mejor['distancia_aprox_m']} m\n")
    print("-" * 135)
    print(f"\n🔄 Actualizando cada {INTERVALO_SEGUNDOS} s | Última actualización: {ultima_actualizacion}")
    print("   (Ctrl+C para detener)\n")
    print("-" * 135)


def main():
    limpiar_pantalla()
    print(TITULO)
    input("Pulsa Enter para comenzar...")

    esperar_placa()  # Bloquea hasta que detecta la placa en el puerto

    ruta_txt, ruta_csv = preparar_ficheros_log()  # Un solo par de ficheros para toda la sesión
    bssids_registrados = set()  # BSSID ya escritos en el log durante esta sesión

    # Bucle de actualización automática: escanea, muestra, registra, espera, repite.
    while True:
        redes = escaneo_redes()
        ultima_actualizacion = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Solo se registran (TXT/CSV) los puntos de acceso que no hayan
        # aparecido ya antes en esta sesión; la pantalla sigue mostrando
        # todo lo detectado en el escaneo actual.
        redes_nuevas = filtrar_nuevas(redes, bssids_registrados)

        mostrar_resultados(redes, ultima_actualizacion, len(bssids_registrados))

        registrar_redes_txt(redes_nuevas, ruta_txt)
        registrar_redes_csv(redes_nuevas, ruta_csv)

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Captura Ctrl+C para salir limpio sin traceback feo
        print("\n\nOperación cancelada por el usuario.")
        sys.exit(0)