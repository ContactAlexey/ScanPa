import subprocess
import ast
import sys
from datetime import datetime

PUERTO = "COM3"

TITULO = r"""
╔════════════════════════════════════════════════════════════════╗
║                                                                  ║
║                                                                  ║
║   ███████╗ ██████╗ █████╗ ███╗   ██╗                            ║
║   ██╔════╝██╔════╝██╔══██╗████╗  ██║                            ║
║   ███████╗██║     ███████║██╔██╗ ██║                            ║
║   ╚════██║██║     ██╔══██╗██║╚██╗██║                            ║
║   ███████║╚██████╗██║  ██║██║ ╚████║                            ║
║   ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝                            ║
║                                                                  ║
║   ██████╗   █████╗                                              ║
║   ██╔══██╗ ██╔══██╗                                             ║
║   ██████╔╝ ███████║                                             ║
║   ██╔═══╝  ██╔══██║                                             ║
║   ██║      ██║  ██║                                             ║
║   ╚═╝      ╚═╝  ╚═╝                                             ║
║                                                                  ║
║                                                                  ║
║          📡   e s c a n e a n d o   p u n t o s   d e           ║
║                       a c c e s o   📡                          ║
║                                                                  ║
║          ──────────────────────────────────────────             ║
║              w i f i   ·   n e t w o r k   ·   s c a n          ║
║                                                                  ║
╚════════════════════════════════════════════════════════════════╝
"""

# Mapeo de los códigos de seguridad que devuelve MicroPython
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


def formatear_mac(bssid_bytes):
    return ':'.join(f'{b:02x}' for b in bssid_bytes)


def calidad_señal(rssi):
    """Convierte RSSI (dBm) a un % aproximado de calidad, y a una etiqueta"""
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
    """
    Estima la distancia en metros a partir del RSSI.

    Calibrado con un único punto de referencia real:
      - MOVISTAR_6930_EXT: -59 dBm a ~0.60 m, con una pared de ~39 cm de por medio

    n se ha fijado a 2.0 (espacio casi libre) por no disponer de un segundo
    punto de calibración; RSSI_1m se ha despejado a partir de la medición real.

    rssi_1m: RSSI de referencia a 1 metro (ajustado a este entorno)
    n: exponente de pérdida de trayecto (fijado, no calculado)
    """
    return round(10 ** ((rssi_1m - rssi) / (10 * n)), 2)


def placa_conectada(puerto=PUERTO):
    """Comprueba si la placa MicroPython responde en el puerto indicado."""
    try:
        resultado = subprocess.run(
            ["python", "-m", "mpremote", "connect", puerto, "exec", "print('ok')"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return resultado.returncode == 0 and "ok" in resultado.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def esperar_placa():
    """Bloquea hasta que la placa esté conectada, avisando al usuario."""
    while not placa_conectada():
        print(f"\n❌ ERROR: No se ha detectado ninguna placa en el puerto {PUERTO}.")
        print("   Conecta la placa por USB y pulsa Enter para reintentar (o Ctrl+C para salir)...")
        input()
    print(f"\n✅ Placa detectada correctamente en {PUERTO}. Iniciando escaneo...\n")


def escaneo_redes():
    resultado = subprocess.run(
        [
            "python", "-m", "mpremote", "connect", PUERTO, "exec",
            "import network; sta = network.WLAN(network.STA_IF); sta.active(True); print(sta.scan())"
        ],
        capture_output=True,
        text=True
    )

    if resultado.stderr:
        print("Errores: ", resultado.stderr)
        return []

    redes_raw = ast.literal_eval(resultado.stdout.strip())
    redes = []

    for ssid, bssid, canal, rssi, seguridad, oculta in redes_raw:
        calidad, etiqueta = calidad_señal(rssi)
        redes.append({
            "ssid": ssid.decode("utf-8", errors="ignore") or "(oculta / sin nombre)",
            "bssid": formatear_mac(bssid),
            "canal": canal,
            "banda": "2.4 GHz" if canal <= 14 else "5 GHz",
            "rssi": rssi,
            "calidad_pct": calidad,
            "calidad_txt": etiqueta,
            "distancia_aprox_m": estimar_distancia(rssi),
            "seguridad": SEGURIDAD.get(seguridad, f"Desconocida ({seguridad})"),
            "oculta": "Sí" if oculta else "No"
        })

    return redes


def registrar_redes(redes):
    nombre = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + " registro.txt"
    with open(nombre, "w", encoding="utf-8") as file:
        for red in redes:
            file.write(
                f"{red['ssid']} | {red['bssid']} | canal {red['canal']} | "
                f"{red['rssi']} dBm | {red['calidad_txt']} | ~{red['distancia_aprox_m']} m | "
                f"{red['seguridad']} | oculta: {red['oculta']}\n"
            )


def mostrar_resultados(redes):
    if not redes:
        print("No se detectaron redes.")
        return

    redes_ordenadas = sorted(redes, key=lambda r: r["rssi"], reverse=True)

    print(f"\n{'SSID':<20} {'BSSID':<18} {'Canal':<7} {'RSSI':<7} {'Calidad':<12} {'Dist. (m)':<10} {'Seguridad':<18} {'Oculta'}")
    print("-" * 110)

    for red in redes_ordenadas:
        print(f"{red['ssid']:<20} {red['bssid']:<18} {red['canal']:<7} "
              f"{red['rssi']:<7} {red['calidad_txt']:<12} {red['distancia_aprox_m']:<10} "
              f"{red['seguridad']:<18} {red['oculta']}")

    mejor = redes_ordenadas[0]
    print("-" * 110)
    print(f"\nRed más potente: {mejor['ssid']} | {mejor['rssi']} dBm | "
          f"Calidad: {mejor['calidad_txt']} ({mejor['calidad_pct']}%) | "
          f"Distancia aprox.: {mejor['distancia_aprox_m']} m\n")


def main():
    print(TITULO)
    input("Pulsa Enter para comenzar...")

    esperar_placa()

    redes = escaneo_redes()
    registrar_redes(redes)
    mostrar_resultados(redes)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperación cancelada por el usuario.")
        sys.exit(0)
