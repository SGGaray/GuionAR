# GuionAR

**Teleprompter overlay para Linux, controlado por texto en tiempo real.**

GuionAR es un panel flotante, translúcido y siempre visible que muestra texto en formato teleprompter cerca de la cámara. Está pensado para leer con fluidez mientras mirás al lente, sin ventanas que tapen la pantalla ni software pesado corriendo de fondo.

Nace como companion de [ParlAR](https://github.com/SGGaray), un sistema de dictado por voz en español, local-first, para Linux. Pero funciona con cualquier fuente de texto que pueda escribir JSON en un socket Unix.

---

## ¿Para qué sirve?

- **Creadores de contenido**: grabá videos a cámara leyendo tu texto justo debajo del lente, sin desviar la mirada.
- **Presentaciones y clases virtuales**: mantené tus notas visibles durante una llamada sin compartirlas por accidente ni perder contacto visual.
- **Streaming**: overlay liviano que convive con OBS y no compite por CPU con el encoder.
- **Lectura asistida en vivo**: si otro sistema (como ParlAR) transcribe lo que se dice, GuionAR lo muestra en pantalla en el momento, línea por línea.

---

## ¿Por qué no es un teleprompter común?

La mayoría de los teleprompters hacen scroll a velocidad fija: el texto avanza aunque te trabes, te adelantes o hagas una pausa. GuionAR invierte esa lógica.

- **El texto te sigue a vos.** El scroll avanza solo cuando hay actividad de voz (señal VAD) y se detiene en los silencios. Si dejás de hablar, el texto espera.
- **Control en tiempo real.** El contenido no está precargado: llega en vivo desde otro proceso por socket, palabra por palabra.
- **Bajo consumo real.** El timer de render corre únicamente mientras hay una animación en curso. En reposo, el uso de CPU es prácticamente cero, apto para sesiones largas de grabación o streaming.
- **Diseño mínimo.** Sin cuentas, sin nube, sin ventanas de configuración. Un panel, texto legible, y nada más.

---

## Features

**Renderizado tipo teleprompter.** La línea actual se muestra grande y centrada, en alto contraste. Las líneas anteriores quedan arriba, más chicas y con fade progresivo, para no perder el hilo sin distraer.

**Scroll suave dirigido por voz.** Cuando una línea se completa, la vista avanza con una animación fluida cuya velocidad podés ajustar en vivo. Con VAD en silencio, en pausa manual o con el mouse sobre el panel, el movimiento se congela.

**Overlay pensado para cámara.** Ventana sin bordes, fondo semitransparente con opacidad configurable, siempre encima de las demás ventanas, posicionada por defecto arriba y al centro (zona de la webcam). Se mueve arrastrando y se redimensiona desde la esquina inferior derecha.

**Arquitectura desacoplada.** El pipeline de dictado y el overlay son procesos independientes que se comunican por un socket Unix. El cliente es fire-and-forget: si GuionAR no está corriendo, los mensajes se descartan en silencio y el pipeline nunca se bloquea ni se entera.

**Robustez ante errores y entradas hostiles.** Todo lo que entra por el socket se valida: JSON malformado, tipos incorrectos y mensajes desconocidos se ignoran. Hay límites de tamaño por mensaje (64 KB), truncado de texto (2000 caracteres), troceo de palabras patológicas y límite de 200 mensajes por segundo. Un cliente roto no puede tirar abajo el overlay, y si el socket falla, el overlay sigue funcionando en modo standalone.

**Modo fantasma.** Con una tecla el panel se vuelve invisible salvo una píldora tenue que permite restaurarlo, útil para despejar la pantalla sin cerrar nada.

---

## Cómo usarlo

### Instalación

Requiere Linux (X11 o Wayland) y Python 3.10+.

```bash
git clone https://github.com/SGGaray/GuionAR.git
cd GuionAR
pip install -r requirements.txt
```

### Modo demo (sin pipeline)

Para verlo funcionando con dictado simulado:

```bash
python guionar.py --demo
```

### Modo producción (con socket)

```bash
python guionar.py --socket
```

Esto abre un socket Unix en `$XDG_RUNTIME_DIR/guionar.sock` (fallback: `/tmp/guionar-<uid>.sock`) y queda esperando texto.

### Opciones de línea de comandos

| Flag | Descripción |
|---|---|
| `--demo` | Dictado simulado, sin pipeline |
| `--socket` | Escucha mensajes del pipeline por socket Unix |
| `--socket-path RUTA` | Ruta alternativa para el socket |
| `--opacity 0.0-1.0` | Opacidad del fondo del panel (default 0.55) |
| `--font-size PT` | Tamaño de fuente de la línea actual (default 30) |

---

## Integración con ParlAR

El flujo es simple: ParlAR escucha y transcribe, GuionAR muestra.

```
Micrófono → ParlAR (VAD + transcripción) → socket Unix → GuionAR (overlay)
```

1. Levantá GuionAR en modo socket:

```bash
python guionar.py --socket
```

2. Del lado de ParlAR, usá el cliente incluido (no depende de Qt, podés copiar la clase directamente al pipeline):

```python
from bridge import TeleprompterClient

prompter = TeleprompterClient()

# En el callback de VAD:
prompter.send_vad(is_speaking)

# En el callback de transcripción (segmento final):
prompter.send_text(texto_final)

# Opcional, para parciales en streaming:
prompter.send_partial(texto_parcial)
```

Los parciales se muestran en gris como vista previa después del texto confirmado: cada parcial reemplaza al anterior y desaparece cuando llega el texto final, así no hay duplicación ni saltos visuales.

El protocolo completo (JSON por líneas, usable desde cualquier lenguaje) y el modo in-process están documentados en [INTEGRATION.md](INTEGRATION.md).

---

## Controles

| Entrada | Acción |
|---|---|
| `+` / `-` | Subir / bajar velocidad de scroll |
| `Espacio` | Pausar / reanudar |
| `T` | Modo fantasma: oculta la ventana de verdad. Una vez oculta, `T` ya no llega (sin foco); restaurar por el mensaje de socket `toggle` (ver Integración) |
| `Flecha arriba` / `abajo` | Agrandar / achicar fuente |
| `C` | Limpiar texto |
| `Ctrl+Q` | Salir |
| Arrastrar el panel | Mover ventana |
| Arrastrar esquina inferior derecha | Redimensionar |
| Hover del mouse | Pausa mientras el cursor está sobre el panel |

Los atajos funcionan cuando el overlay tiene foco. Para control global (con otra app en foco), podés atar atajos de tu entorno de escritorio a mensajes por socket, por ejemplo:

```bash
echo '{"type":"toggle"}' | socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/guionar.sock
```

---

## Arquitectura

```
┌──────────────────────┐   socket Unix    ┌───────────────────────────────┐
│  Pipeline (ParlAR)   │  JSON por líneas │  Proceso GuionAR              │
│  cualquier lenguaje  ├─────────────────▶│  bridge.py: SocketBridge      │
│  TeleprompterClient  │  fire-and-forget │   │ valida / trunca / limita  │
└──────────────────────┘                  │   ▼ señales Qt encoladas      │
                                          │  guionar.py:                  │
                                          │  TeleprompterOverlay (UI)     │
                                          └───────────────────────────────┘
```

Dos módulos, responsabilidades separadas:

- **`guionar.py`**: la ventana, el renderizado, la lógica de scroll, los controles y el CLI.
- **`bridge.py`**: el transporte. `SocketBridge` (servidor del socket Unix), `PipelineBridge` (señales Qt thread-safe para integración in-process) y `TeleprompterClient` (el emisor, sin dependencias).

El hilo que lee el socket nunca toca la UI: todo cruce de hilos pasa por señales Qt encoladas, así que el hilo de interfaz nunca se bloquea por el dictado, ni el dictado por el render.

---

## Consideraciones

**Wayland.** El overlay sin bordes, translúcido y always-on-top funciona en X11 y Wayland (KDE, GNOME con Qt 6.5+, wlroots). Mover y redimensionar usan la vía aprobada por el compositor (`startSystemMove` / `startSystemResize`), así que el drag funciona también en Wayland. La única limitación: Wayland no permite que las ventanas se posicionen solas, por lo que la ubicación inicial arriba-centro puede ser ignorada por algunos compositores. Soluciones: arrastralo una vez, forzá X11 con `QT_QPA_PLATFORM=xcb python guionar.py --socket`, o fijalo con una regla de ventana de tu compositor.

**Atajos globales.** Qt solo recibe teclas con el overlay en foco; el control global se resuelve con atajos del entorno de escritorio apuntando al socket (ver Controles).

**Seguridad del socket.** El socket se crea con permisos `0600` en el directorio runtime del usuario, no en rutas world-writable.

---

## Mini roadmap

- Integración completa con ParlAR: arranque conjunto y detección automática del overlay.
- Modo guion: precargar un texto completo y avanzarlo por voz, además del modo dictado en vivo.
- Presets de lectura: combinaciones guardadas de tamaño, opacidad y velocidad.
- Atajos globales nativos, sin depender de la configuración del escritorio.
- Configuración persistente entre sesiones.

---

## Licencia

MIT
