# GuionAR

GuionAR es un teleprompter flotante para escritorios Linux. Recibe texto de
otro proceso mediante un socket Unix y lo presenta cerca de la cámara en una
ventana translúcida. También puede seguir la posición de lectura sobre un guion
preparado.

El proyecto apunta a Linux con Python 3.10 o posterior y Qt 6. La ventana usa
las APIs de Qt para X11 y Wayland, pero su posición inicial, el comportamiento
*always on top* y el movimiento pueden variar según el compositor.

## Instalación

```bash
git clone https://github.com/SGGaray/GuionAR.git
cd GuionAR
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Para probar el overlay con texto simulado:

```bash
python guionar.py --demo
```

Para recibir texto de un pipeline externo:

```bash
python guionar.py --socket
```

La ayuda del CLI contiene la lista vigente de opciones:

```bash
python guionar.py --help
```

Algunos ejemplos útiles:

```bash
# Personalizar esta ejecución
python guionar.py --socket --opacity 0.4 --font-size 36

# Guardar opacidad y tamaños de fuente para próximos arranques
python guionar.py --opacity 0.4 --font-size 36 --guardar-config

# Seguir un texto preparado
python guionar.py --socket --guion mi_charla.txt
```

La configuración visual se guarda en
`$XDG_CONFIG_HOME/guionar/config.json`, o en
`~/.config/guionar/config.json` cuando esa variable no está definida.

## Modos de lectura

### Modo Dictado

Sin `--guion`, cada mensaje final agrega texto al transcript. Los mensajes
parciales aparecen como una hipótesis temporal y son reemplazados por el
siguiente parcial o terminados por un mensaje final.

El scroll se anima cuando hay actividad de voz (`vad`) y queda detenido durante
el silencio, una pausa manual o el hover del mouse.

### Modo Script

`--guion RUTA` carga un archivo UTF-8 y sigue el texto confirmado sobre ese
guion. La palabra actual se resalta, lo ya leído se atenúa y la vista acompaña
al cursor. Los parciales se muestran, pero no modifican la posición.

El matching usa una ventana local de ocho palabras y nunca retrocede de forma
automática. `Av Pág` y `Re Pág` permiten corregir la posición por oración. Las
líneas que comienzan con `>` se ignoran. Si el archivo no puede leerse o no
contiene palabras, GuionAR informa el problema y continúa en Modo Dictado.

## Controles

| Entrada | Acción |
|---|---|
| `+` / `-` | Ajustar la velocidad de scroll |
| `Espacio` | Pausar o reanudar |
| `T` | Ocultar la ventana cuando existe un canal externo de recuperación |
| `Flecha arriba` / `abajo` | Aumentar o reducir la fuente |
| `C` | Limpiar el transcript; en Modo Script conserva guion y posición |
| `Av Pág` / `Re Pág` | Ir a la oración siguiente o anterior en Modo Script |
| `Ctrl+Q` | Salir |
| Arrastrar | Mover la ventana |
| Arrastrar la esquina inferior derecha | Redimensionar |
| Hover | Pausar mientras el puntero está sobre el panel |

Los atajos de teclado sólo funcionan mientras el overlay tiene foco. Ghost
oculta la ventana realmente, por lo que `T` deja de llegar después de ocultarla.
GuionAR sólo habilita esa acción cuando el socket arrancó correctamente y un
mensaje externo `toggle` puede restaurarla. Sin ese canal, `T` no oculta la
ventana.

## Integración

El cliente incluido no importa Qt:

```python
from guionar_client import TeleprompterClient

prompter = TeleprompterClient()
prompter.send_vad(True)
prompter.send_partial("hipótesis")
prompter.send_text("texto confirmado")
```

El protocolo detallado, sus límites y sus garantías *best effort* están en
[INTEGRATION.md](INTEGRATION.md).

## Arquitectura

```text
productor de texto
       │
       │ JSONL / socket Unix
       ▼
bridge.py · SocketBridge
       │ señales Qt
       ▼
guionar.py · TeleprompterOverlay
       ├── Modo Dictado
       └── Modo Script ── guion.py · Guion
```

- `guionar.py`: CLI, ventana, estado visual y renderizado.
- `guion.py`: normalización y cursor de Modo Script, sin Qt.
- `bridge.py`: servidor Unix y puente de señales hacia la UI.
- `guionar_client.py`: cliente JSONL *best effort*, sólo stdlib.
- `guionar_config.py`: validación y persistencia atómica de configuración.

## Limitaciones conocidas

- GuionAR está orientado a Linux; no ofrece soporte específico para otros
  sistemas operativos.
- No se certificó el comportamiento en todos los compositores X11/Wayland. La
  suite automática usa Qt en modo offscreen y no sustituye pruebas de escritorio
  reales.
- Wayland puede ignorar la posición inicial solicitada por una aplicación. En
  ese caso, mové el panel manualmente o configurá una regla del compositor.
- Los atajos no son globales. Para restaurar Ghost con otra aplicación en foco,
  usá un atajo del entorno de escritorio que envíe `toggle` por el socket.
- El transporte no confirma ni reenvía mensajes; si el overlay no está
  disponible, el cliente puede descartarlos.

## Licencia

[MIT](LICENSE) © 2026 Sebastian Garay.
