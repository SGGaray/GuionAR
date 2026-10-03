# GuionAR

GuionAR es un teleprompter flotante para escritorios Linux. Muestra un guion
cerca de la cámara en una ventana translúcida, siempre visible, y lo hace
avanzar a la velocidad elegida. Si un pipeline de voz como ParlAR está
conectado, sigue la posición de lectura según lo que vas diciendo. Sin guion,
también puede mostrar dictado en vivo recibido por un socket Unix.

El uso normal es abrir GuionAR, cargar un documento (TXT, Markdown, PDF o
DOCX), revisar el texto y darle Play.

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

Para abrir GuionAR y cargar un documento desde la ventana:

```bash
python guionar.py
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

# Abrir directamente un documento
python guionar.py --guion mi_charla.pdf

# Seguir un documento con la voz de ParlAR
python guionar.py --socket --guion mi_charla.docx
```

La configuración visual se guarda en
`$XDG_CONFIG_HOME/guionar/config.json`, o en
`~/.config/guionar/config.json` cuando esa variable no está definida.

## Documentos

El botón **Abrir guion** (o `Ctrl+O`) abre el selector de archivos del
sistema. También se puede arrastrar un archivo sobre la ventana o pasarlo con
`--guion`. Los tres caminos usan el mismo cargador y aceptan:

| Formato | Qué se lee |
|---|---|
| `.txt` | Texto UTF-8 (con o sin BOM). |
| `.md`, `.markdown` | El texto, sin marcas de formato. El HTML embebido se descarta; nunca se interpreta. |
| `.pdf` | La capa de texto, página por página. No hay OCR. |
| `.docx` | Los párrafos del cuerpo, incluidas tablas y cuadros de texto. No se ejecutan macros ni se abre contenido externo. |

Todo se procesa localmente. La extracción corre fuera del hilo de la interfaz,
así que un documento grande no congela la ventana. El nombre del documento
activo aparece arriba a la derecha.

Un documento nuevo reemplaza al anterior sin reiniciar la aplicación: el
cursor vuelve al comienzo y la vista sube al inicio. Abierto desde la ventana
(botón, `Ctrl+O` o arrastrando), el teleprompter queda en pausa, listo para
empezar; `--guion` conserva su comportamiento histórico y no fuerza la pausa.
Si el archivo está vacío, dañado, tiene un formato no soportado o es un PDF
escaneado sin texto, GuionAR lo informa y conserva el documento que estaba
abierto.

## Modos de lectura

### Modo Dictado

Sin `--guion`, cada mensaje final agrega texto al transcript. Los mensajes
parciales aparecen como una hipótesis temporal y son reemplazados por el
siguiente parcial o terminados por un mensaje final.

El scroll se anima cuando hay actividad de voz (`vad`) y queda detenido durante
el silencio, una pausa manual o el hover del mouse.

### Modo Script

Con un documento cargado, lo ya leído se atenúa, la posición actual se destaca
con una marca a la izquierda y el texto próximo queda legible pero en segundo
plano. El avance depende de si hay voz conectada:

- **Sin ParlAR:** Play hace avanzar el guion a velocidad constante y se
  destaca la línea actual. Al llegar al final se detiene; Play vuelve a
  empezar desde el comienzo.
- **Con ParlAR:** el texto confirmado mueve el cursor sobre el guion y se
  destaca la palabra reconocida. Los parciales se muestran, pero no modifican
  la posición. La vista acompaña al cursor mientras hay actividad de voz.
  Si ParlAR se desconecta, GuionAR vuelve al avance automático desde la misma
  posición.

El matching usa una ventana local de ocho palabras y nunca retrocede de forma
automática. `Av Pág`, `Re Pág` o los botones de oración permiten corregir la
posición en cualquiera de los dos casos. Cambiar el tamaño de la ventana o del
texto conserva la posición. Las líneas que comienzan con `>` son notas: no se
muestran ni participan del seguimiento.

Un chip arriba a la izquierda resume el estado: listo para empezar, leyendo,
siguiendo la voz, esperando voz, en pausa o fin del guion.

## Controles

Al mover el puntero sobre el panel aparece una barra compacta con: abrir
documento, oración anterior, Play/Pausa, oración siguiente, velocidad y tamaño
de texto. Cuando el puntero sale, la barra se oculta para no tapar la lectura.
Los botones y los atajos usan la misma lógica, así que se pueden combinar.

| Entrada | Acción |
|---|---|
| `Ctrl+O` | Abrir un documento |
| Soltar un archivo sobre la ventana | Abrir ese documento |
| `+` / `-` | Ajustar la velocidad |
| `Espacio` | Play o pausa; al final del guion, empezar de nuevo |
| `T` | Ocultar la ventana cuando existe un canal externo de recuperación |
| `Flecha arriba` / `abajo` | Aumentar o reducir la fuente |
| `C` | Limpiar el transcript; en Modo Script conserva guion y posición |
| `Av Pág` / `Re Pág` | Ir a la oración siguiente o anterior en Modo Script |
| `Tab` | Recorrer los botones de la barra; `Enter` activa el botón con foco |
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
guionar.py · TeleprompterOverlay ── ui_controls.py · barra y botones
       ├── Modo Dictado
       └── Modo Script ── guion.py · Guion
                 ▲
document_loader.py · TXT / MD / PDF / DOCX → texto normalizado → Guion
```

- `guionar.py`: CLI, ventana, estado visual y renderizado.
- `ui_controls.py`: barra de controles y botones; delega en el overlay.
- `document_loader.py`: extracción y normalización de documentos, sin Qt
  salvo QtPdf para PDF.
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
- Los PDF escaneados (sin capa de texto) no se pueden leer: GuionAR no hace
  OCR. Las columnas múltiples o maquetaciones complejas pueden extraerse en un
  orden imperfecto.
- Los archivos de texto deben estar en UTF-8.
- De un DOCX se leen los párrafos del cuerpo; encabezados, pies de página,
  notas al pie y comentarios no se incluyen.
- La lectura de PDF usa QtPdf, incluido en las wheels de PyQt6. Algunas
  distribuciones empaquetan ese módulo por separado.
- El tamaño de texto y la velocidad elegidos desde la ventana valen para la
  sesión; `--guardar-config` persiste opacidad y tamaños de fuente.

## Licencia

[MIT](LICENSE) © 2026 Sebastian Garay.
