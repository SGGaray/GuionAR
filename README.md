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

### Abrir sin terminal

Para agregar GuionAR al menú de aplicaciones del usuario actual, con su
ícono:

```bash
packaging/linux/install-desktop-entry.sh
```

El script copia el ícono a `~/.local/share/icons` y crea
`~/.local/share/applications/guionar.desktop`, que ejecuta `bin/guionar
--socket` desde esta copia del repositorio (con el `.venv` del repo si
existe). La ruta se calcula al instalar: si movés el repositorio, volvé a
ejecutarlo. `--uninstall` quita la entrada y el ícono.

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

La configuración se guarda en `$XDG_CONFIG_HOME/guionar/config.json`, o en
`~/.config/guionar/config.json` cuando esa variable no está definida. Los
cambios hechos desde la ventana se guardan solos; los flags del CLI valen
sólo para esa ejecución, salvo con `--guardar-config`.

## Configuración

El botón de engranaje (arriba a la derecha) o **Configuración…** en la
bandeja abren una ventana chica, no modal. Los cambios se aplican en vivo y
se recuerdan para el próximo arranque.

**Apariencia**

- **Alineación** del guion: izquierda, centro (por defecto) o derecha.
  Afecta el corte de líneas, el resaltado y la marca de lectura.
- **Opacidad del fondo**, de 0 a 100 %. El texto mantiene su opacidad; con
  el fondo muy transparente se le agrega un contorno oscuro fino para que
  siga legible sobre ventanas claras.
- **Tamaño de texto**, el mismo que ajustan los botones A− / A+.
- **Restaurar valores** devuelve sólo estas opciones a sus valores por
  defecto.

**Comportamiento**

- **Pausar al pasar el mouse** (desactivado por defecto): el puntero
  muestra los controles y sólo pausa si esta opción está activa.
- **Bloquear posición y tamaño**: evita mover o redimensionar la ventana
  por accidente. Los botones siguen funcionando y, si intentás arrastrarla,
  GuionAR avisa que está bloqueada.
- **Recordar posición y tamaño** (activado por defecto). Si la posición
  guardada quedó fuera de las pantallas disponibles, GuionAR la corrige.
- **Ocultar controles automáticamente** (activado por defecto). Desactivado,
  los controles quedan siempre visibles.

## Bandeja del sistema y ventana

Mientras GuionAR está abierto aparece un ícono en la bandeja del sistema. Su
menú permite mostrar u ocultar la ventana, bloquear la posición, activar la
pausa con el puntero, abrir Configuración y salir. Un click en el ícono
trae la ventana. Si ParlAR está enviando voz, el menú lo indica.

Arriba a la derecha, al interactuar, aparecen los controles de la ventana:

| Botón | Acción |
|---|---|
| Candado | Bloquear o desbloquear posición y tamaño |
| Ocultar | Ocultar la ventana; GuionAR sigue corriendo en la bandeja |
| Engranaje | Abrir Configuración |
| Cerrar | Salir de GuionAR |

Ocultar requiere la bandeja: si el escritorio no la ofrece, el botón queda
deshabilitado para que la ventana no quede inaccesible. Ocultar es distinto
de Ghost (`T`), que depende del socket para volver.

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
el silencio, una pausa manual o, si está activada, la pausa con el puntero.

### Modo Script

Con un documento cargado, lo ya leído se atenúa, la posición actual se destaca
con una marca junto al comienzo de la línea y el texto próximo queda legible
pero en segundo plano. El avance depende de si hay voz conectada:

- **Sin ParlAR:** Play hace avanzar el guion a velocidad constante y se
  destaca la línea actual. Al llegar al final se detiene; Play vuelve a
  empezar desde el comienzo.
- **Con ParlAR:** el texto confirmado mueve el cursor sobre el guion y la
  palabra reconocida se subraya. Los parciales aparecen aparte, en una
  pastilla tenue abajo, y no modifican la posición. La vista acompaña al cursor mientras hay actividad de voz.
  Si ParlAR se desconecta, GuionAR vuelve al avance automático desde la misma
  posición.

El matching usa una ventana local de ocho palabras y nunca retrocede de forma
automática. `Av Pág`, `Re Pág` o los botones de oración permiten corregir la
posición en cualquiera de los dos casos. En ventanas muy anchas las líneas se
limitan a unos 70 caracteres para que la vista no tenga que recorrer todo el
ancho. Cambiar el tamaño de la ventana o del
texto conserva la posición. Las líneas que comienzan con `>` son notas: no se
muestran ni participan del seguimiento.

Un chip arriba a la izquierda resume el estado: listo para empezar, leyendo,
siguiendo la voz, esperando voz, en pausa o fin del guion.

## Controles

Al mover el puntero sobre el panel aparece una barra compacta abajo con:
abrir documento, oración anterior, Play/Pausa, oración siguiente, velocidad y
tamaño de texto. Cuando el puntero sale, la barra se oculta para no tapar la
lectura. Los botones y los atajos usan la misma lógica, así que se pueden
combinar.

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
| `Tab` | Mostrar y recorrer los controles; `Enter` activa el botón con foco |
| `Ctrl+Q` | Salir |
| Arrastrar | Mover la ventana (salvo con la posición bloqueada) |
| Arrastrar la esquina inferior derecha | Redimensionar (salvo con la posición bloqueada) |
| Hover | Mostrar los controles; pausa sólo si está activado en Configuración |

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
- `ui_controls.py`: barra de controles, controles de ventana y botones;
  delega en el overlay.
- `desktop_shell.py`: ícono, ventana de Configuración y bandeja del sistema.
- `document_loader.py`: extracción y normalización de documentos, sin Qt
  salvo QtPdf para PDF.
- `guion.py`: normalización y cursor de Modo Script, sin Qt.
- `bridge.py`: servidor Unix y puente de señales hacia la UI.
- `guionar_client.py`: cliente JSONL *best effort*, sólo stdlib.
- `guionar_config.py`: validación y persistencia atómica de configuración.
- `assets/`, `bin/guionar`, `packaging/linux/`: ícono, lanzador y entrada de
  menú para Linux.

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
- La velocidad elegida vale para la sesión; no se guarda entre arranques.
- La bandeja depende del escritorio. GNOME no la muestra sin una extensión
  de indicadores (por ejemplo AppIndicator); sin bandeja GuionAR funciona
  igual, pero no se puede ocultar la ventana.
- En Wayland el compositor puede ignorar la posición restaurada.
- El lanzador es para usar GuionAR desde una copia del repositorio; todavía
  no hay paquetes de distribución.

## Licencia

[MIT](LICENSE) © 2026 Sebastian Garay.
