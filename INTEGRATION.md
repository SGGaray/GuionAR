# GuionAR integration

This document is the authoritative contract for feeding GuionAR from another
process. The public transport is newline-delimited JSON over a local Unix
domain socket.

## Start the socket server

```bash
python guionar.py --socket
```

The default endpoint is selected as follows:

1. `$XDG_RUNTIME_DIR/guionar.sock` when `XDG_RUNTIME_DIR` names an existing
   directory;
2. `/tmp/guionar-<uid>.sock` otherwise.

Use `--socket-path RUTA` to select another path. GuionAR sets the socket mode to
`0600`. If the path belongs to an active listener or is not a Unix socket,
GuionAR leaves it untouched, reports the conflict and continues without IPC.

The server accepts up to eight concurrent connections. It is local-only: it
does not open a TCP port or expose the protocol over the network.

## Python client

`guionar_client.py` uses only the Python standard library:

```python
from guionar_client import TeleprompterClient

prompter = TeleprompterClient()
prompter.send_text("confirmed text")
prompter.send_partial("in-flight hypothesis")
prompter.send_vad(True)
prompter.send_clear()
prompter.send_toggle()
```

`from bridge import TeleprompterClient` remains available for compatibility,
but importing `bridge` also imports Qt.

The client is non-blocking and best effort. If it cannot connect or send, it
drops that message, closes its cached connection and attempts to reconnect on a
later send. It does not buffer, replay or acknowledge messages. Applications
that require delivery confirmation must provide it outside this protocol.

Pass an explicit path when GuionAR uses `--socket-path`:

```python
prompter = TeleprompterClient("/run/user/1000/my-guionar.sock")
```

## Wire format

Each frame is one UTF-8 JSON object followed by `\n`:

```json
{"type": "text", "data": "hello world"}
{"type": "partial", "data": "hel"}
{"type": "vad", "data": true}
{"type": "clear"}
{"type": "toggle"}
```

| Type | Data | Semantics |
|---|---|---|
| `text` | string | Finalizes the current partial. Usable text is appended in Dictation Mode or advances the Script cursor. Empty or whitespace-only text only clears the partial. |
| `partial` | string | Replaces the ephemeral hypothesis. It never advances the Script cursor. An empty string clears it. |
| `vad` | boolean or integer | Updates speaking/idle state and controls scroll animation. Integers are converted to boolean. |
| `clear` | none | Clears Dictation content. In Script Mode it clears transient content without resetting the script cursor or viewport. |
| `toggle` | none | Shows or hides the overlay. Hiding is accepted only when the socket listener is available as an external recovery path. |

Malformed JSON, non-object roots, unsupported data types and unknown message
types are ignored. There is no response or ACK.

Messages from one connection are parsed in stream order. Multiple connections
are handled independently, so their relative ordering is not guaranteed.

## Limits and loss behavior

- A JSON line larger than 64 KiB is discarded.
- An unterminated input buffer larger than 256 KiB is reset.
- Final text keeps at most its first 2,000 characters.
- Partial text keeps at most its last 2,000 characters.
- Quotas use fixed one-second windows shared across all clients:
  - 200 final `text` messages;
  - 200 lossy `partial` or unknown messages;
  - 32 control messages (`vad`, `clear`, `toggle`).
- Messages beyond a quota are dropped without acknowledgement.

These bounds reduce pressure on the local UI; they are not delivery guarantees. Producers
should treat partials as replaceable state and avoid relying on every partial
being displayed.

## Restoring Ghost externally

When the socket listener starts successfully, an external `toggle` can restore
a hidden overlay. For example, bind this command to a desktop shortcut:

```bash
python -c 'from guionar_client import TeleprompterClient; TeleprompterClient().send_toggle()'
```

If you selected a custom socket path, construct the client with that same path.
When GuionAR runs without a working socket, it refuses to hide because an
in-window shortcut cannot restore a window that no longer has focus.

## In-process Qt integration

A Python application that already runs Qt may bypass the Unix socket:

```python
from PyQt6.QtWidgets import QApplication
from bridge import PipelineBridge
from guionar import TeleprompterOverlay

app = QApplication([])
overlay = TeleprompterOverlay()
bridge = PipelineBridge(overlay)
overlay.show()

# These methods may be called from a producer thread. Qt delivers the
# corresponding slots on the UI thread while the event loop is running.
bridge.push_text("confirmed text")
bridge.push_partial("hypothesis")
bridge.push_vad(True)

app.exec()
```

This mode depends on the Qt event loop and does not provide the socket's input
validation, connection limits or rate limits.
