# GuionAR integration

This document is for developers who want another program to send text to
GuionAR. You do not need it to use GuionAR or to use it with ParlAR: see the
[README](README.md).

It is the authoritative contract for that integration. The transport is
newline-delimited JSON over a local Unix domain socket.

## Socket

GuionAR opened from the applications menu already listens on the socket. When
GuionAR is started from a terminal, the listener is enabled with `--socket`.

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

The client is non-blocking and best effort. If it cannot connect or send, it
drops that message, closes its cached connection and attempts to reconnect on a
later send. It does not buffer, replay or acknowledge messages. Applications
that require delivery confirmation must provide it outside this protocol.

### Staying connected (recommended for voice producers)

With `auto_connect=True` a single background thread owns the connection:
it connects as soon as GuionAR is available, notices immediately when
GuionAR closes and retries every 1-2 seconds while it is absent. It waits
on blocking primitives (no polling), so the idle cost is negligible. Sends
never connect or block: without a connection they are dropped. Only state
transitions are logged (`[guionar] conectado` / `[guionar] desconectado`).

```python
prompter = TeleprompterClient(auto_connect=True, client_name="parlar",
                              on_state=lambda connected: ...)
...
prompter.close()   # bounded, clean shutdown
```

`on_state` runs on the connection thread; GUI applications must forward it
to their own UI thread. `client_name` makes the client send `hello` on
every (re)connection (see below).

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
{"type": "hello", "client": "parlar", "role": "voice-producer", "protocol": 1}
```

| Type | Data | Semantics |
|---|---|---|
| `text` | string | Finalizes the current partial. Usable text is appended in Dictation Mode or advances the Script cursor. Empty or whitespace-only text only clears the partial. |
| `partial` | string | Replaces the ephemeral hypothesis. It never advances the Script cursor. An empty string clears it. |
| `vad` | boolean or integer | Updates speaking/idle state and controls scroll animation. Integers are converted to boolean. |
| `clear` | none | Clears Dictation content. In Script Mode it clears transient content without resetting the script cursor or viewport. |
| `toggle` | none | Shows or hides the overlay. Hiding is accepted only when the socket listener is available as an external recovery path. |
| `hello` | `client` (string, 1-64 chars), `role`, `protocol` | Optional, once per connection. With `role` `voice-producer` GuionAR shows the producer as connected before it speaks. It never changes reading behavior, grants nothing and is not authentication. Other roles or invalid fields are ignored. |

`hello` is backward compatible: older GuionAR versions ignore it as an
unknown type, and clients that never send it keep working exactly as
before (they are recognized as voice producers when they send `text`,
`partial` or `vad`).

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
  - 32 control messages (`vad`, `clear`, `toggle`, `hello`).
- Messages beyond a quota are dropped without acknowledgement.

These bounds reduce pressure on the local UI; they are not delivery guarantees. Producers
should treat partials as replaceable state and avoid relying on every partial
being displayed.

## Hidden window

A `toggle` message shows or hides the GuionAR window. It is the way to bring
back a window hidden with the `T` key, which GuionAR only allows while the
socket listener is running, so that this recovery path always exists.
