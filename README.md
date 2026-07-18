<div align="center">
  <img src="./logo/scanpa_logo.png" alt="ScanPa" width="500">
</div>

<h2 align="center">📡 w i f i &nbsp;·&nbsp; n e t w o r k &nbsp;·&nbsp; s c a n 📡</h2>

<div align="center">

![Python](https://img.shields.io/badge/Python-3.x-3776AB?style=flat-square&logo=python&logoColor=white)
![MicroPython](https://img.shields.io/badge/MicroPython-compatible-2B2728?style=flat-square&logo=micropython&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-ESP32%20%2F%20ESP8266-00979D?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)
![Status](https://img.shields.io/badge/Status-Activo-brightgreen?style=flat-square)

</div>

<p align="center">
  <b>ScanPa</b> es una herramienta de línea de comandos que utiliza una placa MicroPython (ESP32 / ESP8266) conectada por USB para <b>escanear puntos de acceso WiFi</b> cercanos y mostrar un informe claro y ordenado directamente en tu terminal.
</p>

---

## 📑 Índice

- [Características](#-características)
- [Requisitos](#-requisitos)
- [Instalación](#-instalación)
- [Uso](#-uso)
- [Ejemplo de salida](#-ejemplo-de-salida)
- [Cómo funciona](#-cómo-funciona)
- [Estructura del proyecto](#-estructura-del-proyecto)
- [Solución de problemas](#-solución-de-problemas)
- [Roadmap](#-roadmap)
- [Licencia](#-licencia)

---

## ✨ Características

- 🔍 **Escaneo de redes WiFi** cercanas usando el chip WiFi de la propia placa MicroPython.
- 📶 **Cálculo de calidad de señal** (RSSI convertido a porcentaje y etiqueta descriptiva).
- 🔐 **Detección del tipo de seguridad** de cada red (Abierta, WEP, WPA, WPA2, WPA3...).
- 🆔 Muestra **SSID, BSSID (MAC), canal, banda (2.4/5 GHz)** y si la red está oculta.
- 🏆 Identifica automáticamente la **red con mejor señal**.
- ✅ **Comprobación automática de la placa**: si no está conectada, te avisa y espera a que la conectes antes de escanear.
- 🎨 Interfaz de terminal con **título ASCII** y mensajes claros de estado.

---

## 🧰 Requisitos

- **Python 3.x** instalado en tu ordenador.
- Librería [`mpremote`](https://pypi.org/project/mpremote/) instalada:

  ```bash
  pip install mpremote
  ```

- Una placa compatible con **MicroPython** (ESP32 o ESP8266) con firmware de MicroPython ya cargado.
- Cable USB de datos (no solo de carga) para conectar la placa al ordenador.

---

## 📦 Instalación

1. Clona este repositorio:

   ```bash
   git clone https://github.com/tu-usuario/scanpa.git
   cd scanpa
   ```

2. Instala las dependencias:

   ```bash
   pip install mpremote
   ```

3. Conecta tu placa MicroPython por USB.

4. (Opcional) Comprueba el puerto en el que se ha conectado tu placa y ajústalo en el script si es necesario (ver [Configuración](#-uso)).

---

## 🚀 Uso

Ejecuta el script desde la terminal:

```bash
python scanpa.py
```

El programa mostrará el título de bienvenida y esperará a que pulses **Enter** para comenzar:

```
Pulsa Enter para comenzar...
```

A continuación comprobará si la placa está conectada:

- ❌ Si **no detecta la placa**, mostrará un error y esperará a que la conectes, reintentando cada vez que pulses Enter.
- ✅ Si la placa **está conectada**, comenzará el escaneo automáticamente y mostrará los resultados.

### ⚙️ Configurar el puerto

Por defecto, el script usa el puerto `COM3` (Windows). Si tu placa se conecta en otro puerto, edita esta línea al principio del script:

```python
PUERTO = "COM3"   # Cambia por el puerto de tu placa
```

> En Linux/Mac el puerto suele ser algo como `/dev/ttyUSB0` o `/dev/cu.usbserial-XXXX`.

---

## 🖥️ Ejemplo de salida

```
SSID                 BSSID              Canal   RSSI    Calidad      Seguridad          Oculta
----------------------------------------------------------------------------------------------------
MiRed_5G             aa:bb:cc:dd:ee:ff  36      -45     Excelente    WPA2-PSK           No
Vecino_WiFi          11:22:33:44:55:66  6       -62     Muy buena    WPA/WPA2-PSK       No
CAFE_FREE_WIFI       77:88:99:aa:bb:cc  11      -78     Débil        Abierta (sin ...)  No
----------------------------------------------------------------------------------------------------

Red más potente: MiRed_5G | -45 dBm | Calidad: Excelente (100%)
```

---

## ⚙️ Cómo funciona

1. **Conexión con la placa**: ScanPa utiliza `mpremote` para comunicarse con la placa MicroPython vía el puerto serie/USB.
2. **Verificación previa**: antes de escanear, se envía un comando de prueba (`print('ok')`) a la placa para confirmar que responde correctamente.
3. **Escaneo WiFi**: se activa la interfaz `network.WLAN(network.STA_IF)` en la placa y se ejecuta `scan()`, que devuelve la lista de redes detectadas.
4. **Procesamiento**: los datos recibidos (SSID, BSSID, canal, RSSI, seguridad, visibilidad) se decodifican y se calcula la calidad de la señal.
5. **Visualización**: los resultados se ordenan de mayor a menor potencia de señal y se muestran en una tabla en la terminal.

---

## 🗂️ Estructura del proyecto

```
scanpa/
├── scanpa.py          # Script principal
├── logo/
│   └── scanpa_logo.png
├── README.md
└── requirements.txt
```

---

## 🛠️ Solución de problemas

| Problema | Posible causa | Solución |
|---|---|---|
| `No se ha detectado ninguna placa` | Puerto incorrecto o placa no conectada | Verifica el cable USB y el valor de `PUERTO` en el script |
| `mpremote` no encontrado | No está instalado | Ejecuta `pip install mpremote` |
| No aparecen redes | WiFi de la placa no soporta modo STA | Verifica que tu placa (ESP32/ESP8266) soporte `network.STA_IF` |
| Caracteres extraños en SSID | Codificación del nombre de red | El script ya maneja errores de decodificación con `errors="ignore"` |

---

## 🗺️ Roadmap

- [ ] Exportar resultados a CSV/JSON
- [ ] Modo de escaneo continuo (monitorización en tiempo real)
- [ ] Soporte multiplataforma automático para detección de puerto
- [ ] Interfaz gráfica (GUI) opcional
- [ ] Alertas de redes nuevas/desconocidas

---

## 📄 Licencia

Este proyecto está bajo la licencia **MIT**. Consulta el archivo `LICENSE` para más detalles.

<div align="center">

---

Hecho con 📡 y 🐍 por AlexeyTechSec

</div>
