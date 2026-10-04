# Changelog: GuionAR

## [Unreleased]

## [0.2.0] - 2026-10-04

### Agregado
- **Experiencia de escritorio completa**: entrada en el menú de
  aplicaciones, ventana con controles propios (mantener encima, bloquear,
  ocultar, configuración y cerrar) y barra de controles que aparece al pasar
  el mouse.
- **Documentos**: abre TXT, Markdown, PDF y DOCX desde un botón, con `Ctrl+O`
  o arrastrando el archivo a la ventana. Si un archivo está vacío o dañado,
  avisa y conserva el documento abierto.
- **Configuración** en una ventana propia: alineación, opacidad del fondo,
  tamaño de texto, pausa al pasar el mouse, bloqueo y memoria de posición y
  tamaño. Los cambios se aplican al momento y se recuerdan.
- **Ícono en la bandeja del sistema** para mostrar u ocultar GuionAR, abrir
  la Configuración y ver el estado de ParlAR.
- **Integración con ParlAR sin configurar nada**: se conectan solos en
  cualquier orden y GuionAR vuelve a aceptar a ParlAR si se reinicia, sin
  perder la posición del guion.
- **Seguir la voz**: el guion avanza a medida que lo leés con ParlAR y
  subraya la palabra reconocida.
- **Seguimiento parcial**: lo que todavía se está reconociendo aparece aparte,
  sin mover la posición hasta que se confirma.
- **Transcript en vivo**: sin guion cargado, muestra lo que dictás con
  ParlAR mientras hablás.
- **Identidad visual propia**: ícono de la aplicación, ícono de bandeja y
  Configuración con la marca de GuionAR.
- **Release descargable** con suma SHA256: se instala con `./install.sh`, sin
  clonar el repositorio, en Python 3.12, 3.13 o 3.14. `guionar-uninstall`
  desinstala sin necesitar la release; con `--purge-data` borra también la
  configuración.
- **`guionar --version`** muestra la versión instalada.

### Cambiado
- **Vista de lectura**: la línea actual queda en una posición estable, lo
  leído se atenúa y la vista sólo se mueve cuando hace falta.
- Las actualizaciones instalan la versión nueva al lado de la anterior y
  sólo la activan cuando quedó completa; si algo falla, la versión vigente
  no cambia.

### Corregido
- Mayor estabilidad al iniciar, al cerrar y al reconectar con ParlAR.
- La configuración se guarda de forma atómica y un archivo inválido no
  impide abrir GuionAR.
