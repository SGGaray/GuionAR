# Changelog

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
El proyecto usa versionado semántico.

## [Unreleased]

### Agregado

- Modo Script con carga UTF-8, matching local, resaltado, navegación manual y
  viewport adaptado al cursor.
- Cliente standalone `guionar_client.py`, sin dependencia de Qt.
- Suites de regresión para configuración, IPC, lifecycle, render y ambos modos
  de lectura; el workflow de CI ejecuta el conjunto completo.

### Cambiado

- El layout de Modo Script usa métricas de fuente y conserva la posición al
  redimensionar o cambiar el tamaño de texto.
- La pintura de guiones largos recorre únicamente el rango visible con
  overscan.
- Ghost sólo permite ocultar la ventana cuando existe un socket funcional que
  pueda restaurarla desde otro proceso.
- Los límites de tráfico separan texto final, parciales y controles, con cuotas
  acotadas por ventanas fijas.

### Corregido

- Carga segura de configuraciones ausentes, malformadas, no UTF-8 o con valores
  fuera de rango.
- Escritura atómica de configuración, preservando el archivo anterior si falla
  la escritura temporal o el reemplazo.
- Propiedad y limpieza segura del socket, rechazo de endpoints activos o ajenos,
  cierre determinista de listener/clientes y límite de conexiones concurrentes.
- Terminación cooperativa por `SIGINT` y liberación del endpoint antes de un
  reinicio.
- Cursor terminal, reflow y alineación del viewport en Modo Script.
- Actualización visual del indicador VAD al pasar de activo a inactivo.
- Un mensaje final vacío termina el parcial sin agregar contenido ni avanzar el
  guion.

## [0.1.4] - 2026-07

### Corregido

- Ghost pasó a ocultar realmente la ventana y a restaurarse mediante el mensaje
  de socket `toggle`.
- Se agregó el workflow inicial de GitHub Actions.

## [0.1.3] - 2026-07

### Agregado

- Persistencia de opacidad y tamaños de fuente mediante `--guardar-config`.

## [0.1.2] - 2026-07

### Agregado

- Suite inicial de pruebas del overlay y del socket.
- Método `TeleprompterClient.send_toggle()`.

## [0.1.1] - 2026-07

### Cambiado

- `FlowDictateBridge` fue renombrado a `PipelineBridge` y la ventana adoptó el
  nombre GuionAR.
- El socket pasó a aceptar varias conexiones y el contador de tráfico quedó
  protegido para uso concurrente.

## [0.1.0] - 2026-07

### Agregado

- Primera versión del overlay translúcido y sin bordes para Linux.
- Modo Dictado con texto final, parciales, VAD y scroll animado.
- IPC local mediante JSONL sobre socket Unix.
- Controles de velocidad, fuente, pausa, movimiento y tamaño.
