# Arquitectura: ParlAR + GuionAR

Este documento explica cómo funciona el sistema completo de dictado con teleprompter: qué hace cada parte, cómo se comunican, y por qué tomé las decisiones que tomé. Lo escribo también para mi yo del futuro, que va a querer tocar esto en seis meses sin acordarse de nada.

## La idea en una línea

Hablo, ParlAR transcribe localmente con Whisper y escribe el texto en la ventana que tenga foco. Si además tengo GuionAR abierto, el mismo texto aparece en un overlay tipo teleprompter cerca de la cámara, y el scroll avanza solo cuando estoy hablando.

## El flujo completo

```
micrófono
   │  frames PCM 16kHz
   ▼
VAD (webrtcvad)          ¿hay voz o silencio?
   │  segmentos de voz
   ▼
faster-whisper           transcripción local (GPU si hay)
   │  texto crudo
   ▼
procesador de texto      puntuación española, muletillas, comandos de voz
   │  texto limpio
   ├──────────────► inyector (xdotool/wtype) ──► ventana con foco
   │
   └──────────────► cliente GuionAR ──► socket Unix ──► GuionAR (overlay)
```

Las dos salidas son independientes: el inyector es la función principal (el texto tiene que llegar a la app), y GuionAR es una salida secundaria opcional que se activa con `--guionar`.

## Por qué dos procesos separados en vez de una sola app

Fue la decisión más importante y la tomé por tres razones concretas:

1. **Si el overlay se cuelga o crashea, el dictado no puede caerse.** ParlAR escribe en mis ventanas; un bug de renderizado en un panel Qt no puede interrumpir eso. Al ser procesos separados, el peor caso es que el teleprompter desaparece y sigo dictando.
2. **No quería meter PyQt6 como dependencia de ParlAR.** ParlAR ya carga faster-whisper y CUDA; sumarle un framework de UI entero para una feature opcional lo hacía más pesado y más frágil de instalar.
3. **Me obliga a definir un contrato.** Al comunicarse por socket con mensajes JSON, cualquier otra cosa puede alimentar a GuionAR en el futuro (un lector de guiones, otro motor de STT), y GuionAR no sabe ni le importa quién le habla.

El costo de esta decisión es real: hay que levantar dos procesos (lo resuelvo con un script launcher) y hay ~30 líneas de cliente socket duplicadas en ParlAR en vez de un paquete compartido. Acepté la duplicación a propósito: un paquete compartido acoplaría los releases de los dos repos, y el protocolo es tan chico que mantenerlo a mano en dos lugares es más simple. La especificación del protocolo vive en un solo lugar (INTEGRATION.md de GuionAR) y es la fuente de verdad.

## El protocolo

JSON por líneas sobre un socket Unix (`$XDG_RUNTIME_DIR/guionar.sock`, permisos 0600):

```json
{"type": "text",    "data": "hola mundo"}     texto confirmado, se agrega
{"type": "partial", "data": "hipótesis..."}   vista previa, se reemplaza
{"type": "vad",     "data": true}             hablando / en silencio
{"type": "clear"}                             limpiar pantalla
{"type": "toggle"}                            mostrar/ocultar overlay
```

La distinción text/partial existe porque ParlAR en modo streaming usa LocalAgreement: decodifica el audio varias veces y solo "confirma" las palabras que aparecen iguales en dos pasadas seguidas. Lo confirmado va como `text` (blanco, definitivo), y la cola todavía inestable va como `partial` (gris, puede cambiar). Así el teleprompter nunca muestra como definitivo algo que Whisper puede retractar.

## Cómo se protege cada lado del otro

Del lado de ParlAR, el cliente es fire-and-forget: socket no bloqueante, si GuionAR no está los mensajes se descartan (~10 µs), si la conexión se corta se reintenta en el próximo envío, y nunca lanza excepciones hacia el pipeline. Además deduplica: el estado VAD solo se manda cuando cambia, y los parciales solo cuando difieren del anterior.

Del lado de GuionAR, todo lo que entra por el socket se valida antes de tocar la UI: JSON malformado se ignora, los mensajes tienen tope de 64 KB, el texto se trunca a 2000 caracteres, palabras sin espacios se trocean para que el renglón siempre pueda cerrar, y hay un límite de 200 mensajes por segundo. Un cliente roto o malicioso no puede tirar el overlay. La asunción de seguridad es que quien escribe en el socket es el mismo usuario de la sesión (por eso el 0600 en el runtime dir); no hay autenticación porque no cruza usuarios ni la red.

## Cómo funciona el hilo de UI en GuionAR

El hilo que lee el socket nunca toca los widgets. Emite señales de Qt, que al venir de otro hilo se encolan y se ejecutan en el hilo de UI. Es el mecanismo estándar de Qt para cruzar hilos y me evitó todos los problemas de locks.

Para el consumo de CPU, la regla es: el timer de render (60 fps) solo corre mientras hay una animación de scroll en curso. En cuanto el scroll llega a destino, o se pausa (silencio, hover, tecla), el timer se detiene. En reposo el proceso no hace nada.

## Qué haría distinto / deuda conocida

- El socket de GuionAR atiende de a un cliente por vez; con ParlAR conectado, un segundo cliente (por ejemplo un atajo de teclado del escritorio) no puede entrar. Está en el checklist arreglarlo con un hilo por conexión.
- La inyección de texto en ParlAR sigue acoplada al pipeline; el cliente GuionAR fue el primer output desacoplado y la idea es que la inyección termine igual.
- GuionAR no persiste configuración entre sesiones todavía; cada arranque son los defaults más los flags.
