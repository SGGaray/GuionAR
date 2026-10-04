<p align="center">
  <img src="assets/guionar.svg" width="96" height="96" alt="Ícono de GuionAR">
</p>

# GuionAR

**Teleprompter flotante para Linux.** Cargás tu guion y avanza solo, o sigue
tu voz con [ParlAR](https://github.com/SGGaray/ParlAR).

Funciona sin conexión a internet y no captura audio.

## Qué hace

- **Teleprompter siempre visible.** Una ventana translúcida que queda encima
  de las demás, para ponerla cerca de la cámara.
- **Abre tus documentos**: TXT, Markdown, PDF y DOCX, desde un botón o
  arrastrando el archivo a la ventana.
- **Avance automático** a la velocidad que elijas, con la línea actual
  destacada y lo ya leído atenuado.
- **Sigue tu voz** con ParlAR: el guion avanza a medida que lo leés en voz
  alta, y vuelve al avance automático si ParlAR se cierra.
- **Transcript en vivo**: sin guion cargado, muestra lo que vas dictando con
  ParlAR.
- **Controles a mano**: velocidad, tamaño de texto, oración anterior y
  siguiente, con botones o con el teclado.
- **Configuración** de apariencia y comportamiento, que se recuerda para la
  próxima vez.

## Documentos soportados

| Formato | Qué se lee |
|---|---|
| `.txt` | El texto. Tiene que estar en UTF-8. |
| `.md`, `.markdown` | El texto, sin las marcas de formato. |
| `.pdf` | El texto de cada página. **No lee PDF escaneados** (sin texto seleccionable). |
| `.docx` | Los párrafos del cuerpo, incluidas tablas y cuadros de texto. No incluye encabezados, pies de página, notas al pie ni comentarios. |

- Los PDF con varias columnas o diseño complejo pueden leerse en un orden
  imperfecto.
- Las líneas que empiezan con `>` se toman como notas para vos: no se
  muestran.
- Si un archivo está vacío, dañado o no tiene texto, GuionAR lo avisa y
  conserva el documento que tenías abierto.
- En ventanas muy anchas las líneas se limitan a unos 70 caracteres para que
  la lectura sea cómoda.

## Compatibilidad

| | Estado |
|---|---|
| Linux con X11 | Soportado. |
| Linux con Wayland | Funciona, pero el escritorio puede ignorar la posición de la ventana o el «siempre visible». |
| Bandeja del sistema | Opcional. En GNOME hace falta una extensión de indicadores. |
| Windows, macOS | No soportados. |

Necesitás Python 3.12 o posterior y PyQt6.

## Instalar

Por ahora GuionAR se usa desde una copia de este repositorio. La entrada del
menú apunta a esa carpeta, así que conservala en un lugar fijo.

**1. Paquetes del sistema**

```bash
# Debian / Ubuntu
sudo apt install git python3 python3-venv

# Fedora
sudo dnf install git python3
```

**2. Descargar GuionAR y sus dependencias**

```bash
git clone https://github.com/SGGaray/GuionAR.git
cd GuionAR
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Esto deja las dependencias dentro de la carpeta `GuionAR`, sin tocar el
resto del sistema.

**3. Agregar GuionAR al menú de aplicaciones**

```bash
packaging/linux/install-desktop-entry.sh
```

**4. Abrir GuionAR** desde el menú de aplicaciones.

Si después movés la carpeta `GuionAR` a otro lugar, repetí el paso 3.

## Empezar

**Con un guion**

1. Abrí GuionAR desde el menú.
2. Tocá **Abrir guion** (o `Ctrl+O`), o arrastrá el archivo a la ventana.
3. Dale **Play** (o `Espacio`).

**Con tu voz, siguiendo un guion**

1. Abrí GuionAR y cargá el guion.
2. Abrí ParlAR. Se conectan solos, en cualquier orden.
3. Empezá a leer en voz alta.

**Transcript en vivo, sin guion**

1. Abrí GuionAR sin cargar un documento.
2. Abrí ParlAR y dictá.
3. GuionAR muestra lo que vas diciendo.

## Modos

### Guion con avance automático

Play hace avanzar el guion a velocidad constante. La línea actual se
destaca, lo leído se atenúa y lo que viene queda legible en segundo plano. Al
llegar al final se detiene; Play vuelve a empezar desde el comienzo.

### Guion siguiendo tu voz (con ParlAR)

Lo que decís mueve la posición sobre el guion y subraya la palabra
reconocida. Lo que todavía se está reconociendo aparece aparte, abajo, sin
mover la posición. GuionAR nunca retrocede solo: si te salteás o repetís una
parte, corregí con `Av Pág` / `Re Pág` o con los botones de oración. Si
ParlAR se cierra, GuionAR sigue con el avance automático desde el mismo
lugar.

### Transcript en vivo (sin guion)

Sin un documento cargado, GuionAR muestra lo que dictás con ParlAR: lo ya
dicho queda arriba y la frase en curso aparece a medida que hablás. La vista
se mueve sólo mientras hay voz.

Arriba a la izquierda, un indicador resume el estado: listo para empezar,
leyendo, siguiendo la voz, esperando voz, en pausa o fin del guion.

## Controles

Al pasar el mouse sobre la ventana aparece una barra abajo con: abrir
documento, oración anterior, Play/Pausa, oración siguiente, velocidad y
tamaño de texto. Arriba a la derecha aparecen los controles de la ventana:
mantener encima, bloquear, ocultar, configuración y cerrar.

| Tecla o gesto | Acción |
|---|---|
| `Ctrl+O` | Abrir un documento |
| Soltar un archivo sobre la ventana | Abrir ese documento |
| `Espacio` | Play o pausa; al final del guion, empezar de nuevo |
| `+` / `-` | Más o menos velocidad |
| `Flecha arriba` / `abajo` | Texto más grande o más chico |
| `Av Pág` / `Re Pág` | Oración siguiente o anterior |
| `C` | Limpiar el transcript (con guion, conserva el guion y la posición) |
| `Tab` | Recorrer los controles; `Enter` activa el que tiene foco |
| `Ctrl+Q` | Salir |
| Arrastrar la ventana | Moverla (salvo con la posición bloqueada) |
| Arrastrar la esquina inferior derecha | Cambiar el tamaño (salvo con la posición bloqueada) |

Los atajos funcionan mientras la ventana de GuionAR tiene el foco.

**Ocultar** deja GuionAR funcionando en la bandeja del sistema; un clic en
el ícono de la bandeja lo trae de vuelta. Si tu escritorio no tiene bandeja,
el botón queda deshabilitado para que la ventana no se pierda.

## Configuración

El engranaje de la ventana, o **Configuración…** en el menú de la bandeja,
abre la Configuración. Los cambios se aplican al momento y se recuerdan.

**Apariencia**
- **Alineación** del guion: izquierda, centro (por defecto) o derecha.
- **Opacidad del fondo**, de 0 a 100 %. Con el fondo muy transparente el
  texto gana un contorno fino para seguir legible.
- **Tamaño de texto**.
- **Restaurar valores** de apariencia.

**Comportamiento**
- **Mantener GuionAR sobre otras ventanas** (activado por defecto).
- **Pausar al pasar el mouse** (desactivado por defecto).
- **Bloquear posición y tamaño**, para no moverla por accidente.
- **Recordar posición y tamaño** (activado por defecto).
- **Ocultar controles automáticamente** (activado por defecto).

**ParlAR**
- Muestra si ParlAR está conectado.

La velocidad elegida vale para la sesión y no se guarda.

## Usar con ParlAR

[ParlAR](https://github.com/SGGaray/ParlAR) es un dictado por voz local para
Linux. No hay que configurar nada:

- Si los dos están abiertos, se conectan solos, en cualquier orden.
- Si ParlAR se cierra, GuionAR sigue funcionando como teleprompter y lo
  vuelve a aceptar cuando reaparece, sin perder la posición del guion.
- El menú de la bandeja indica si ParlAR no está, está conectado o está
  siguiendo tu voz.

Mientras GuionAR está conectado, ParlAR por defecto le envía el dictado sólo
a GuionAR y no lo escribe en otras aplicaciones. Eso se cambia en la
Configuración de ParlAR.

## Privacidad

- **No usa la red.** GuionAR no se conecta a internet ni a ningún servicio.
- **Tus documentos se leen en tu computadora.** GuionAR no los copia ni
  recuerda cuáles abriste.
- **No captura audio.** El reconocimiento de voz lo hace ParlAR; GuionAR sólo
  recibe el texto, a través de una conexión local accesible sólo para tu
  usuario.
- **El transcript vive sólo en memoria**: como máximo las últimas 200 frases
  o 20.000 caracteres, y se pierde al cerrar GuionAR. No se guarda en disco.
- **Lo único que se guarda** es la configuración (apariencia, comportamiento
  y posición de la ventana), en `~/.config/guionar/config.json`.
- **No escribe archivos de registro.** Sí imprime mensajes breves de estado,
  como la ruta del documento que cargaste; según tu escritorio, esos
  mensajes pueden quedar en el registro de la sesión. Nunca imprime el
  contenido del guion ni lo que dictás.

## Actualizar

Por ahora la actualización se hace en la misma carpeta `GuionAR`:

```bash
cd GuionAR
git pull
.venv/bin/pip install -r requirements.txt
```

La entrada del menú apunta a esa carpeta, así que la próxima vez que abras
GuionAR ya usa la versión nueva. Si estaba abierto, cerralo y volvé a
abrirlo.

## Desinstalar

Desde la carpeta `GuionAR`:

```bash
packaging/linux/install-desktop-entry.sh --uninstall
```

**Elimina:** la entrada del menú de aplicaciones y el ícono.

**Conserva:** tu configuración (`~/.config/guionar/`) y la carpeta `GuionAR`
con sus dependencias.

Para quitarlo del todo, borrá también la carpeta `GuionAR` y, si querés,
tu configuración:

```bash
rm -r ~/.config/guionar
```

## Limitaciones conocidas

- Linux únicamente. No se probó en todos los escritorios X11 y Wayland.
- En Wayland el escritorio puede ignorar la posición de la ventana; movela a
  mano o usá una regla del compositor.
- Los atajos de teclado no son globales: funcionan con la ventana de
  GuionAR en foco.
- Sin bandeja del sistema, GuionAR funciona igual pero no se puede ocultar
  la ventana.
- No lee PDF escaneados (no hace reconocimiento de texto en imágenes).
- Los archivos de texto tienen que estar en UTF-8.
- La velocidad elegida no se guarda entre una sesión y otra.
- Lo que se dicta mientras GuionAR está cerrado no aparece después.
- Todavía no hay paquetes para distribuciones: se usa desde una copia del
  repositorio.

## Estado del proyecto

GuionAR es una aplicación mantenida como producto. Este repositorio contiene
su código fuente y está organizado principalmente para instalarla, usarla e
inspeccionar el código, no como un proyecto de desarrollo comunitario. Por
eso no hay guía de contribución ni roadmap público.

## Código fuente y licencia

El código se publica bajo licencia [MIT](LICENSE) © 2026 Sebastian Garay.

Si querés que otro programa le envíe texto a GuionAR, el protocolo está
documentado en [INTEGRATION.md](INTEGRATION.md).

## Reportar un problema

- **Errores, problemas de instalación o de compatibilidad:** abrí un
  [issue](https://github.com/SGGaray/GuionAR/issues/new/choose) y elegí el
  tipo que corresponda.
- **Vulnerabilidades de seguridad:** no las publiques en un issue. Seguí
  [SECURITY.md](SECURITY.md).
