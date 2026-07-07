# GuionAR - Integration Guide

How to connect GuionAR to a dictation pipeline (ParlAR / FlowDictate).

## Mode A: out-of-process (recommended)

Run the overlay:

```bash
python guionar.py --socket
```

This opens a Unix socket at `$XDG_RUNTIME_DIR/guionar.sock` (fallback `/tmp/guionar-<uid>.sock`), permissions `0600`.

In the pipeline, use the bundled client (no Qt dependency, copy the class if you prefer):

```python
from bridge import TeleprompterClient

prompter = TeleprompterClient()

# transcription callback (final segment):
prompter.send_text(final_text)

# optional streaming partials (replace the current line):
prompter.send_partial(partial_text)

# VAD callback:
prompter.send_vad(is_speaking)
```

Guarantees for the pipeline side:

- Non-blocking socket, fire-and-forget.
- If GuionAR is not running, sends are silently dropped and the client reconnects on the next send. The pipeline can never be slowed down or crashed by the overlay.

Guarantees for the overlay side (hardening):

- Malformed JSON, wrong types and unknown message types are ignored.
- Single messages over 64 KB are dropped; unterminated garbage over 256 KB resets the buffer.
- Text payloads are truncated to 2000 chars; pathological unbroken "words" are chunked so lines always commit.
- Spam guard: max 200 messages/second, excess dropped.
- If the socket cannot be created, the overlay keeps running in standalone mode (degraded, logged to stderr).

### Wire protocol (any language)

Newline-delimited JSON:

```json
{"type": "text",    "data": "hello world"}
{"type": "partial", "data": "hel"}
{"type": "vad",     "data": true}
{"type": "clear"}
{"type": "toggle"}
```

Shell test:

```bash
echo '{"type":"text","data":"hola"}' | socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/guionar.sock
```

### Partial vs final semantics

`text` appends committed text (it wraps into lines and drives the scroll). `partial` shows an in-flight hypothesis as a dim suffix after the committed text; each `partial` replaces the previous one, and it is cleared automatically when `text` arrives (or explicitly with `{"type":"partial","data":""}`). This matches both pipeline styles: full-partial-then-final transcribers, and incremental committers like ParlAR's LocalAgreement streaming, with no duplication in either case.

## Mode B: in-process (pipeline is Python + Qt)

```python
from PyQt6.QtWidgets import QApplication
from guionar import TeleprompterOverlay
from bridge import FlowDictateBridge

app = QApplication([])
overlay = TeleprompterOverlay()
bridge = FlowDictateBridge(overlay)
overlay.show()

# From any thread of your pipeline (never blocks, queued Qt signals):
# bridge.push_text(final_text)
# bridge.push_partial(partial_text)
# bridge.push_vad(is_speaking)

app.exec()
```

## Wayland notes

- Frameless, translucent, always-on-top works via Qt 6 on X11 and Wayland (KDE, GNOME with Qt >= 6.5, wlroots).
- Drag/resize use `startSystemMove()` / `startSystemResize()`, the compositor-approved path, so they work on Wayland.
- Wayland does not let clients position themselves. Initial top-center placement may be ignored on some compositors; drag once, or force X11: `QT_QPA_PLATFORM=xcb python guionar.py --socket`, or pin with a window rule matching the title `FlowDictate Teleprompter` (Hyprland: `windowrule = pin, title:...`).

## Global hotkeys

Qt shortcuts work while the overlay has focus. For system-wide control, bind DE shortcuts (GNOME/KDE custom shortcuts, Hyprland `bind`) to socket messages, e.g.:

```bash
echo '{"type":"toggle"}' | socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/guionar.sock
```
