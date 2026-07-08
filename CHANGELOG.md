# Changelog: GuionAR

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/). Versionado semántico.

## [Unreleased]
### Pendiente (auditoría julio 2026)
- Limpieza: eliminar la clave de config `max_next_lines` (sin uso)
- Tests: portar la suite de validación al repo (`tests/`, headless)

## [0.1.1] - 2026-07
### Corregido
- Renombrado `FlowDictateBridge` → `PipelineBridge`; título de ventana
  "FlowDictate Teleprompter" → "GuionAR". GuionAR es un proyecto nuevo
  y no debía llevar naming del predecesor
- El socket ahora acepta múltiples conexiones simultáneas (un hilo por
  cliente, backlog 8) en vez de una sola. Antes, un segundo cliente (por
  ejemplo un atajo de teclado enviando `{"type":"toggle"}`) no podía
  conectar mientras el pipeline mantenía su conexión abierta, lo que
  rompía en silencio el flujo de atajos documentado
- El rate limiter ahora usa un lock: con múltiples hilos de conexión
  podía haber una condición de carrera sobre el contador compartido

## [0.1.0] - 2026-07
### Agregado
- Release inicial: overlay teleprompter frameless, translúcido,
  always-on-top, para X11 y Wayland
- Renderizado: línea actual grande y centrada, historial con fade,
  vista previa de hipótesis pendiente en gris (semántica de `partial`
  como sufijo efímero)
- Scroll suave dirigido por VAD: avanza al hablar, se congela en
  silencio, en pausa manual o con hover
- Bridge por socket Unix con hardening: validación de tipos, tope de
  64 KB por mensaje, truncado a 2000 caracteres, troceo de palabras
  patológicas, rate limit de 200 msg/s, buffer cap de 256 KB
- Cliente emisor sin dependencias de Qt (fire-and-forget)
- Modo fantasma (tecla T), controles de velocidad/fuente/pausa,
  CLI: --demo, --socket, --socket-path, --opacity, --font-size
- CPU en reposo cercana a cero (timer de render solo durante animación)
- Cierre limpio con Ctrl+C (manejo de SIGINT sobre el event loop de Qt)
