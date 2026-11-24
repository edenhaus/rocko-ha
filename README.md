# rocko for Home Assistant

A Home Assistant integration that makes [rocko](https://github.com/edenhaus/rocko),
a media daemon for IP cameras, the WebRTC provider of your cameras: when a
camera's live view opens in Home Assistant, rocko reads the camera's stream
(RTSP, or HLS and MPEG-TS over HTTP, as far as the daemon's build reads them)
and sends it to the browser over WebRTC.

The integration is written to Home Assistant core's standards so it can move
into core later. Until then it is a custom integration, installable with HACS.

## Use cases

- **Live view in the dashboard.** A camera card or the camera's dialog opens
  a WebRTC session through rocko: the browser plays the camera's stream with
  low latency, without Home Assistant transcoding it.
- **Viewing from outside your home.** Over Home Assistant Cloud, the browser
  gets TURN servers for its connection; Home Assistant passes the same ones to
  rocko, so the live view also works when the browser is not on your network.
- **Fast start for the cameras you open most.** Turn on *Preload stream* in
  a camera's settings: rocko keeps that camera connected without viewers, so
  a live view starts with a picture at once instead of after the camera's
  connection and its next keyframe.
- **A rotated or mirrored mount.** Set the camera's *Orientation*: rocko
  passes it on, and browsers that support it turn the picture.

## Requirements

- Home Assistant 2026.10 or newer.
- A rocko binary on the Home Assistant host (Linux), a rocko that already
  runs and whose control socket Home Assistant may open, or a rocko on
  another host (or in another container) whose control listener Home
  Assistant reaches over the network.
- Cameras whose integration gives Home Assistant a stream source, such as
  Generic Camera, ONVIF or most vendor integrations.

## Installation

With HACS: add `https://github.com/edenhaus/rocko-ha` as a custom repository
of type *Integration*, install *rocko*, and restart Home Assistant.

By hand: copy `custom_components/rocko` into the `custom_components` folder of
your Home Assistant configuration, and restart Home Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration** and pick *rocko*.
Choose how Home Assistant reaches the daemon:

- **Run rocko**: Home Assistant runs the daemon itself.
  - *rocko binary*: the path of the `rocko` executable; the form suggests the
    one on the `PATH`. Home Assistant checks that it is executable and that
    `rocko --version` names rocko.

  Each start gets a fresh private directory for the control socket
  (`rocko serve --socket <dir>/rocko.sock`). The daemon's log goes to Home
  Assistant's log at each line's own level. Home Assistant restarts the
  daemon when it exits or stops answering, and stops it (SIGTERM, then
  SIGKILL after 5 s) when Home Assistant stops or the integration is
  unloaded.
- **Connect to a running rocko**: the daemon runs on its own, for example as
  a service on the host.
  - *Control socket*: the path of its control socket, `/run/rocko/rocko.sock`
    by default. Home Assistant connects to check it before the entry is made.
    The daemon admits only the user it was started as, or the one its
    `--allow-uid` names.
- **Connect to rocko on another host**: the daemon runs elsewhere and serves
  its control API over mutual TLS (see
  [rocko on another host](#rocko-on-another-host)).
  - *Host*: the name or IP address of the host rocko runs on.
  - *Port*: its control listener's TCP port, `18557` by default.
  - *Pairing code*: the one-time code from `rocko pair`, valid for 10
    minutes. Home Assistant makes a new client key and certificate, pairs
    them with the daemon through the code, and connects once to check it
    before the entry is made. The entry keeps the key, its certificate and
    the daemon's certificate, never the code.

Only one rocko entry can exist. To switch between the three, change the
path, host or port, or pair again, use **Reconfigure** on the entry.

## Examples

### Home Assistant runs rocko

With the `rocko` binary at `/usr/local/bin/rocko` on the Home Assistant host
(or in its container):

1. **Add integration → rocko → Run rocko**.
2. Keep the suggested *rocko binary* or enter `/usr/local/bin/rocko`, and
   submit. The entry is created once `rocko --version` names rocko; Home
   Assistant then starts the daemon and connects to it.

### rocko in its own container next to Home Assistant

rocko's release image (`ghcr.io/edenhaus/rocko`, published without a
`latest` tag: pin the release's tag or digest) serves its control socket at
`/run/rocko/rocko.sock`. Share that directory with Home Assistant through a
volume. Both containers run as root, the user rocko admits on its socket, and
rocko drops its privileges after binding. Host networking lets browsers reach
rocko's WebRTC port (UDP 18556) directly.

```yaml
services:
  rocko:
    image: ghcr.io/edenhaus/rocko:<release>
    network_mode: host
    restart: unless-stopped
    volumes:
      - rocko-socket:/run/rocko

  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./config:/config
      - rocko-socket:/run/rocko

volumes:
  rocko-socket:
```

Then **Add integration → rocko → Connect to a running rocko**, and keep the
suggested *Control socket*, `/run/rocko/rocko.sock`.

### rocko on another host

A rocko in its own container without a shared socket, or on another
machine, serves its control API over mutual TLS once its control listener
is on. Each side trusts exactly the other's certificate: the daemon admits
Home Assistant by its certificate's pin, which pairing enrolls, and Home
Assistant trusts exactly the daemon's certificate, which pairing fetches.

1. Start rocko with the listener on and its port published, for example
   with rocko's release image:

   ```sh
   podman run -d --name rocko --read-only --cap-drop=all --security-opt=no-new-privileges \
     --tmpfs /run/rocko:rw,mode=0700,U -v rocko-data:/data \
     -p 30415:30415/udp -p 30415:30415/tcp -p 18557:18557/tcp \
     -e 'ROCKO_CONTROL_LISTEN=[::]:18557' -e ROCKO_WEBRTC_ADVERTISE=192.168.1.10 \
     ghcr.io/edenhaus/rocko:<release>
   ```

   - `ROCKO_CONTROL_LISTEN` turns the control listener on; `18557` is the
     port the form suggests. Home Assistant must reach that TCP port.
   - Browsers, not Home Assistant, receive the video: they must reach the
     daemon's WebRTC port (UDP and TCP 30415 by default) at an address the
     daemon tells them. Behind a container's bridge network the daemon only
     knows the container's own addresses, so `ROCKO_WEBRTC_ADVERTISE` names
     the host's LAN address instead (`IP` or `IP:PORT`); with host
     networking it is not needed. rocko's README explains the networking.
   - The data volume keeps the daemon's certificate and the enrolled
     clients across restarts.

2. Get a pairing code. A rocko that has no client yet logs one at startup
   (`podman logs rocko`); `rocko pair` makes a new one at any time
   (`podman exec rocko /rocko pair`). A code is valid for 10 minutes and
   only the newest one works.
3. **Add integration → rocko → Connect to rocko on another host**, and
   enter the host's address, the port and the code.

If the form says the daemon's certificate lacks the name Home Assistant
checks, the daemon's identity was made by an older rocko: `rocko identity
--reissue` replaces it, and after a restart of rocko you pair with a new
code.

To pair again, for example with a new code after the daemon's identity
changed or the client was revoked, use **Reconfigure → Connect to rocko on
another host** with a new code: Home Assistant pairs a new key and
certificate and reloads the entry. To revoke Home Assistant on the daemon,
remove its pin (*client_pin* in the entry's diagnostics) with
`rocko ctl clients remove <pin>`; a revoked Home Assistant cannot connect
until it pairs again. There is no re-authentication flow yet: a revoked
client shows as a connection error and is retried.

## How it works

- Home Assistant holds one connection to the daemon's control socket, or
  to its control listener over mutual TLS (through Home Assistant's shared
  HTTP session), and pings it every 10 s. A missing pong or a closed connection counts as lost:
  Home Assistant reconnects at once, then after 1, 2, 4, … up to 60 s, and
  restarts a daemon it runs. While the daemon cannot be reached, live views
  fail at once, so the frontend falls back to HLS.
- Each camera is one rocko stream, named after the camera's entity id. Before
  every live view Home Assistant sends the stream's definition again with the
  camera's current stream source, its *orientation* preference (passed to
  browsers that turn the picture themselves) and its *preload stream*
  preference (rocko keeps such a camera connected without viewers).
- Each browser session is one rocko session under Home Assistant's own
  session id, with the ICE servers the browser got, so TURN servers from Home
  Assistant Cloud reach rocko too. Answers, candidates and errors go straight
  back to the browser.
- When the connection is lost, the daemon keeps the sessions for 10 s.
  After the reconnect, and after a reload of the integration while rocko
  keeps running, Home Assistant adopts them again, so live views continue.
- Nothing is polled: the daemon pushes every session event.

## What works

- Live view over WebRTC for every camera whose stream source is an `rtsp`,
  `rtsps`, `http` or `https` URL, if the daemon's build reads that protocol,
  with trickled ICE in both directions. The source keeps rocko's defaults:
  RTSP over TCP, no backchannel, TLS trusting the system's roots.
- Orientation and preload preferences.
- Running and supervising the daemon, connecting to a running one through
  its socket, or to one on another host over mutual TLS, paired with a
  one-time code.
- Diagnostics: the daemon's `info`, its counters and stream states, with
  addresses and credentials redacted; for a rocko on another host, the pins
  of Home Assistant's and the daemon's certificates, with the host, the key
  and the certificates redacted.
- Repair issues when rocko runs with its sandbox off, or without one of its
  layers (privilege drop, `no_new_privs`, seccomp, Landlock filesystem
  rules). They are checked on every connection and withdrawn once fixed.
- Rocko first among the WebRTC providers: a camera whose URL scheme rocko
  reads is rocko's even when another provider, such as go2rtc, reads it too;
  other schemes go to the other provider. Home Assistant has no order for
  providers, so the integration sets it through Home Assistant internals
  until Home Assistant can order them.

## Not supported yet

- Snapshots: the integration does not provide camera images; Home Assistant
  takes them from the camera's own integration or its stream as before.
- Recordings and two-way audio, which rocko supports, are not used yet.
- Repair issues for a stream that keeps failing; the daemon's errors reach
  the browser and the log.
- An options flow; there is nothing to set besides the connection.

## Troubleshooting

- The integration logs under `custom_components.rocko`, the daemon's own
  lines under `custom_components.rocko.daemon`. For more detail:

  ```yaml
  logger:
    logs:
      custom_components.rocko: debug
  ```

- **Download diagnostics** on the entry shows whether the daemon is
  connected, its version, limits, sandbox, counters and streams.
- A rocko on another host that cannot be reached leaves the entry retrying
  its setup, with the client's error on the entry. Check that the port is reachable, and that the daemon
  still lists Home Assistant's pin (`rocko ctl clients list`); if it does
  not, pair again through **Reconfigure**.
- A live view that fails shows rocko's error code in the browser's console
  (`source_auth_failed`, `source_unreachable`, `video_codec_unsupported`, …).
- Stream source URLs carry camera passwords: the integration never logs them,
  only stream ids.

## Removal

Delete the entry under **Settings → Devices & services → rocko**. A daemon
that Home Assistant runs is stopped; from a daemon that runs on its own,
here or on another host, the streams of Home Assistant's cameras are
deleted, and the daemon keeps running. A daemon on another host keeps Home
Assistant's pin enrolled: remove it with `rocko ctl clients remove <pin>`
(the pin is in the entry's diagnostics, or in `rocko ctl clients list`).
Then remove the integration in HACS, or delete `custom_components/rocko`.

## Development

[mise](https://mise.jdx.dev) installs the tools (`mise install`, which also
installs the git hooks), and uv the dependencies, pinned in `uv.lock`.

| Command | What it does |
|---|---|
| `mise run check` | every prek hook on all files: ruff, ruff format, mypy, file hygiene |
| `mise run test` | the tests with the 100 % coverage gate |

The tests use Home Assistant's own fixtures through
`pytest-homeassistant-custom-component`, with the client and the daemon
mocked. CI (`.github/workflows/ci.yml`) also runs hassfest and the HACS
validation.

## License

[Apache-2.0](LICENSE), as Home Assistant core.
