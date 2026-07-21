# Pintland Region Audio System

Plays region-specific SoundCloud music for Minecraft players, with seamless
crossfades between regions and zero manual steps after a one-time browser link.

```
Minecraft Plugin  <--WS-->  Relay Server (Node.js)  <--WS-->  Web Client (browser, dual SC widgets)
   (Java/Paper)                                                
```

- **Plugin** (`/plugin`) — detects WorldGuard region changes and notifies the relay.
- **Relay** (`/relay`) — routes commands to the correct player's browser tab, handles
  auth, ack/retry, and reconnect replay. Also serves the web client.
- **Client** (`/client`) — owns all crossfade logic using two hidden SoundCloud widgets.

All transport is JSON over WebSocket (WSS in production).

---

## Message protocol

**Plugin → Relay**
```json
{ "type": "region_change", "player": "<uuid>", "region": "tavern", "url": "https://soundcloud.com/user/sets/tavern", "volume": 80 }
{ "type": "player_quit", "player": "<uuid>" }
```
**Relay → Client**
```json
{ "type": "play_region", "region": "tavern", "url": "https://soundcloud.com/user/sets/tavern", "volume": 80 }
{ "type": "stop" }
```
**Client → Relay**
```json
{ "type": "ack", "player": "<uuid>", "region": "tavern" }
{ "type": "ping" }
```
**Relay → Plugin** (status)
```json
{ "type": "client_status", "player": "<uuid>", "connected": true }
```

Handshake: the plugin connects to `/plugin` with the `x-plugin-secret` header.
The client connects to `/client?token=<signed-uuid>`; the relay verifies the HMAC
signature before registering the socket.

---

## 1. Relay server

```bash
cd relay
npm install
cp .env.example .env      # then edit PLUGIN_SECRET and TOKEN_SECRET
npm start                 # serves the client at http://localhost:8080/
```

**Environment (`relay/.env`):**

| Variable        | Meaning                                                          |
|-----------------|-----------------------------------------------------------------|
| `PORT`          | Port to listen on (default 8080).                               |
| `PLUGIN_SECRET` | Shared secret the plugin sends in `x-plugin-secret`.            |
| `TOKEN_SECRET`  | HMAC key used to sign/verify player UUID tokens.                |
| `CLIENT_DIR`    | Optional override for the static client dir (default `../client`). |

**Tests:**
```bash
cd relay && npm test        # token signing/verify + routing, ack/retry, reconnect replay, auth
```

**Docker:** the Dockerfile builds from the repo root so it can copy both `relay/` and `client/`:
```bash
docker build -f relay/Dockerfile -t pintland-relay .
docker run --rm -p 8080:8080 --env-file relay/.env pintland-relay
```

### Deploying with WSS (required in production)

Browsers only allow the SoundCloud widget in a secure context, so the client must
be served over HTTPS and the WebSocket over WSS. Run the relay as plain HTTP behind
a TLS-terminating reverse proxy with a Let's Encrypt certificate. Example Caddy config:

```
audio.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy auto-provisions the certificate and upgrades WebSocket connections, so both
`https://audio.example.com/` and `wss://audio.example.com/client?token=…` just work.
(nginx works too — remember `proxy_set_header Upgrade`/`Connection "upgrade"`.)

---

## 2. Web client

Static `index.html` + `app.js` + `style.css`, served by the relay at `/`. No build step.

- Shows a themed **"Tap to enter Pintland"** overlay. The first click/keydown/touch
  satisfies the browser autoplay policy (both widgets do `setVolume(0) → play() → pause()`).
- Commands that arrive before unlock are **queued** and flushed on unlock.
- **Crossfade**: on `play_region`, the idle widget loads the new URL, plays muted, then
  an exponential 1500 ms fade swaps it with the active widget; the client then `ack`s.
- **Reconnect**: WebSocket reconnects with exponential backoff (1→2→4…cap 30 s); the relay
  replays the current region on reconnect. A `ping` is sent every 20 s.

The `token` from the join URL is persisted in `localStorage` so return visits reconnect
without a fresh link.

---

## 3. Minecraft plugin (Paper 1.21, Java 21)

```bash
cd plugin
./gradlew shadowJar        # produces build/libs/pintland-audio-1.0.0.jar
```

> Building requires access to the PaperMC (`repo.papermc.io`) and EngineHub
> (`maven.enginehub.org`) Maven repositories for the `compileOnly` Paper/WorldGuard
> APIs. Only `Java-WebSocket` is shaded into the jar; Paper and WorldGuard are provided
> by the server at runtime.

Drop the jar into `plugins/` alongside **WorldGuard** (and WorldEdit), start the server
once to generate `plugins/PintlandAudio/config.yml` and `regions.yml`, then edit them.

**`config.yml`** — relay URL + shared secret, `web-base-url`, `token-secret`
(must equal the relay's `TOKEN_SECRET`), and hysteresis tuning.

**`regions.yml`** — maps WorldGuard region IDs to SoundCloud sets and volumes:
```yaml
regions:
  tavern:
    url: "https://soundcloud.com/user/sets/tavern"
    volume: 80
  harbor:
    url: "https://soundcloud.com/user/sets/harbor"
    volume: 65
default:
  url: ""      # empty = silence when in no mapped region
  volume: 0
```

**Behavior:**
- `PlayerMoveEvent` early-returns unless the player crossed a block boundary
  (performance-critical).
- Resolves the current region via WorldGuard `RegionQuery#getApplicableRegions`, picking
  the highest-priority region that has a mapping.
- **Hysteresis** prevents doorway flip-flopping: a `region_change` only fires once a
  candidate region has held for `consecutive-crossings` block-crossings **or** the player
  is `blocks-past-boundary` blocks past where it first appeared.
- On join, the player receives a clickable chat link `<web-base-url>/?token=<signed-uuid>`.
  `/pintlandaudio link` reprints it; `/pintlandaudio reload` reloads config.

The plugin's `TokenSigner` and the relay's `src/tokens.js` produce byte-identical tokens
(HMAC-SHA256 of the UUID, unpadded base64url) so join links verify on the relay.

---

## End-to-end test flow

Without a Minecraft server you can exercise the relay + client with the mock scripts:

```bash
# terminal 1 — relay
cd relay && npm start

# print a signed URL and open it in a browser, then click "Tap to enter Pintland"
node scripts/sign-token.js 00000000-0000-0000-0000-000000000001

# terminal 2 — act as the plugin and push a region change to that player
node scripts/mock-plugin.js 00000000-0000-0000-0000-000000000001 tavern \
  https://soundcloud.com/soundcloud/sets/soundcloud-hidden-gems 80
```

The browser tab should load the set and crossfade in; the relay logs the returning `ack`.
`scripts/mock-client.js` provides a headless client (auto-acks) for testing routing,
ack/retry, and reconnect replay without a browser.

Full in-game validation needs a real Paper + WorldGuard server, defined regions, and
SoundCloud sets that the uploader made API-streamable.

## Constraints

- SoundCloud playback only works for tracks the uploader made API-streamable; some
  restricted/Go+ tracks won't play through the widget.
- The one-time first click is unavoidable (browser autoplay policy); it's hidden inside
  the themed "enter" overlay.
- The widget exposes volume but not raw PCM, so the crossfade is volume-based across two
  widgets, not true buffer mixing.
