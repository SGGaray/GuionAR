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

**Requisitos:** Linux x86_64 con glibc 2.34 o posterior (Ubuntu 22.04,
Debian 12, Fedora 35 o más nuevas) y Python 3.12, 3.13 o 3.14 con el módulo
`venv`. El instalador trae PyQt6; no hace falta compilador. Espacio: unos
300 MB por versión instalada (al actualizar se conserva la anterior hasta la
siguiente actualización).

## Instalar

GuionAR se instala desde la release publicada, en tu cuenta de usuario, con
su propio entorno y una entrada en el menú de aplicaciones. No hace falta
`sudo` salvo para los paquetes del sistema.

**1. Paquetes del sistema**

```bash
# Debian / Ubuntu
sudo apt install python3 python3-venv libxcb-cursor0

# Fedora
sudo dnf install python3 xcb-util-cursor
```

**2. Descargar** desde la página de
[releases](https://github.com/SGGaray/GuionAR/releases/latest) el archivo
`guionar-<versión>-linux.tar.gz` y `SHA256SUMS`, en la misma carpeta.

**3. Verificar** que la descarga esté completa y sin modificaciones:

```bash
sha256sum -c --ignore-missing SHA256SUMS
```

Tiene que responder `OK` para el archivo descargado.

**4. Extraer e instalar**

```bash
tar -xzf guionar-*-linux.tar.gz
cd guionar-*/
./install.sh
```

**5. Abrir GuionAR** desde el menú de aplicaciones.

La carpeta extraída ya no hace falta después de instalar: podés borrarla.
GuionAR queda en `~/.local/share/guionar` y los comandos `guionar` y
`guionar-uninstall` en `~/.local/bin`.

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

Descargá y verificá la release nueva como en la instalación, extraela y
ejecutá su instalador:

```bash
tar -xzf guionar-*-linux.tar.gz
cd guionar-*/
./install.sh
```

La versión nueva se instala al lado de la actual y sólo se activa cuando
quedó completa y validada; si algo falla, la versión que tenías sigue
funcionando sin cambios. Tu configuración se conserva. Si GuionAR estaba
abierto, cerralo y volvé a abrirlo desde el menú.

**Si antes usabas GuionAR desde una copia del repositorio** con la entrada
de menú de `packaging/linux/install-desktop-entry.sh`, el instalador
reemplaza esa entrada por la nueva. No borra ni modifica la copia del
repositorio ni su `.venv`: si ya no los usás, podés borrarlos vos.

`guionar --version` muestra la versión instalada.

## Desinstalar

Cerrá GuionAR y ejecutá:

```bash
guionar-uninstall
```

No necesita la carpeta de la release. Si `~/.local/bin` no está en tu
`PATH`: `~/.local/share/guionar/uninstall.sh`.

**Elimina:** la aplicación instalada y sus versiones, los comandos
`guionar` y `guionar-uninstall`, la entrada del menú y el ícono.

**Conserva:** tu configuración (`~/.config/guionar/`). Para borrarla también:

```bash
guionar-uninstall --purge-data
```

Nunca toca una copia del repositorio de GuionAR ni su `.venv`.

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
- Todavía no hay paquetes para distribuciones: se instala desde la release
  para Linux x86_64.

## Estado del proyecto

GuionAR es una aplicación mantenida como producto. Este repositorio contiene
su código fuente y está organizado principalmente para instalarla, usarla e
inspeccionar el código, no como un proyecto de desarrollo comunitario. Por
eso no hay guía de contribución ni roadmap público.

## Código fuente y licencia

El código fuente está en este repositorio y se publica bajo licencia
[MIT](LICENSE) © 2026 Sebastian Garay. Para usar GuionAR, instalá la release.

Si querés que otro programa le envíe texto a GuionAR, el protocolo está
documentado en [INTEGRATION.md](INTEGRATION.md).

## Reportar un problema

- **Errores, problemas de instalación o de compatibilidad:** abrí un
  [issue](https://github.com/SGGaray/GuionAR/issues/new/choose) y elegí el
  tipo que corresponda.
- **Vulnerabilidades de seguridad:** no las publiques en un issue. Seguí
  [SECURITY.md](SECURITY.md).
