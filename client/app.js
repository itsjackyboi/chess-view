/* Pintland web client.
 *
 * Owns all audio: two hidden SoundCloud widgets that we crossfade between as
 * the relay pushes play_region commands. The relay never touches volume; the
 * browser does everything here.
 */
(function () {
  'use strict';

  const CROSSFADE_MS = 1500;
  const PING_MS = 20000;
  const RECONNECT_CAP_MS = 30000;

  // ---- token ---------------------------------------------------------------
  // Prefer the token in the URL (fresh join link); fall back to a stored one so
  // return visits reconnect without a new link.
  const urlToken = new URLSearchParams(location.search).get('token');
  if (urlToken) localStorage.setItem('pintland_token', urlToken);
  const token = urlToken || localStorage.getItem('pintland_token');

  // ---- DOM -----------------------------------------------------------------
  const overlay = document.getElementById('overlay');
  const enterBtn = document.getElementById('enter');
  const statusEl = document.getElementById('status');
  const banner = document.getElementById('banner');

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = 'status' + (kind ? ' ' + kind : '');
  }
  function showBanner(show) {
    banner.hidden = !show;
  }

  if (!token) {
    setStatus('No token — open your join link from Minecraft.', 'err');
    return;
  }

  // ---- SoundCloud widget wrapper ------------------------------------------
  // Wraps SC.Widget so we can await READY and track the widget's current volume
  // (the API is write-only for volume, so we mirror it locally for the fades).
  function makeWidget(iframeId) {
    const widget = SC.Widget(document.getElementById(iframeId));
    const w = {
      widget,
      ready: false,
      volume: 100,
      currentUrl: null,
      _readyResolvers: [],
    };
    widget.bind(SC.Widget.Events.READY, function () {
      w.ready = true;
      w._readyResolvers.splice(0).forEach(function (r) { r(); });
    });
    w.whenReady = function () {
      return w.ready ? Promise.resolve() : new Promise(function (res) { w._readyResolvers.push(res); });
    };
    w.setVolume = function (v) {
      w.volume = v;
      widget.setVolume(v);
    };
    // Loop single tracks; sets/playlists auto-advance on their own.
    widget.bind(SC.Widget.Events.FINISH, function () {
      widget.getCurrentSound(function (sound) {
        widget.getSounds(function (sounds) {
          if (!sounds || sounds.length <= 1) {
            widget.seekTo(0);
            safePlay(widget);
          }
        });
      });
    });
    return w;
  }

  // ---- autoplay unlock -----------------------------------------------------
  let unlocked = false;
  const commandQueue = [];

  // Wrap every play(): browsers reject autoplay until a gesture, and reject
  // again if the tab is throttled. Surface a tap-to-enable banner on rejection.
  function safePlay(widget) {
    try {
      const p = widget.play();
      if (p && typeof p.catch === 'function') {
        p.catch(function () { showBanner(true); });
      }
    } catch (e) {
      showBanner(true);
    }
  }

  let A, B, activeWidget, idleWidget;
  let currentUrl = null;

  async function unlock() {
    if (unlocked) return;
    // Prime BOTH widgets with the muted play/pause the autoplay policy wants.
    await Promise.all([A.whenReady(), B.whenReady()]);
    for (const w of [A, B]) {
      w.setVolume(0);
      safePlay(w.widget);
      w.widget.pause();
    }
    unlocked = true;
    showBanner(false);
    overlay.classList.add('hidden');
    // Flush anything the relay sent while we were still gated.
    const pending = commandQueue.splice(0);
    pending.forEach(function (cmd) { onPlayRegion(cmd.url, cmd.volume, cmd.region); });
  }

  // ---- crossfade engine ----------------------------------------------------
  function crossfade(from, to, targetVol, duration) {
    const fromStartVol = from.volume;
    const start = performance.now();
    return new Promise(function (resolve) {
      function step(now) {
        const t = Math.min(1, (now - start) / duration);
        // Exponential curves; linear fades sound abrupt.
        from.setVolume(fromStartVol * Math.pow(1 - t, 2));
        to.setVolume(targetVol * Math.pow(t, 2));
        if (t < 1) {
          requestAnimationFrame(step);
        } else {
          from.setVolume(0);
          from.widget.pause();
          to.setVolume(targetVol);
          resolve();
        }
      }
      requestAnimationFrame(step);
    });
  }

  function onPlayRegion(url, targetVolume, region) {
    if (!unlocked) {
      commandQueue.push({ url: url, volume: targetVolume, region: region });
      return;
    }
    if (!url) {
      // Empty url = silence (default region). Fade the active widget down.
      if (currentUrl !== null) {
        crossfade(activeWidget, idleWidget, 0, CROSSFADE_MS);
        currentUrl = null;
      }
      sendAck(region);
      return;
    }
    if (url === currentUrl) { sendAck(region); return; }
    currentUrl = url;

    const from = activeWidget;
    const to = idleWidget;
    let played = false;

    to.widget.load(url, {
      auto_play: false,
      callback: function () {
        to.currentUrl = url;
        to.setVolume(0);
        // Wait for the PLAY event before fading so we don't fade into silence.
        const onPlay = function () {
          if (played) return;
          played = true;
          to.widget.unbind(SC.Widget.Events.PLAY);
          crossfade(from, to, targetVolume, CROSSFADE_MS).then(function () {
            activeWidget = to;
            idleWidget = from;
            sendAck(region);
          });
        };
        to.widget.bind(SC.Widget.Events.PLAY, onPlay);
        safePlay(to.widget);
      },
    });
  }

  // ---- websocket -----------------------------------------------------------
  let socket = null;
  let reconnectDelay = 1000;
  let pingTimer = null;
  let currentRegion = null;

  function wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return proto + '//' + location.host + '/client?token=' + encodeURIComponent(token);
  }

  function sendAck(region) {
    const r = region != null ? region : currentRegion;
    if (socket && socket.readyState === WebSocket.OPEN && r != null) {
      socket.send(JSON.stringify({ type: 'ack', region: r }));
    }
  }

  function connect() {
    setStatus('Connecting…');
    socket = new WebSocket(wsUrl());

    socket.onopen = function () {
      reconnectDelay = 1000;
      setStatus(unlocked ? 'Connected' : 'Connected — tap to enter', 'ok');
      clearInterval(pingTimer);
      pingTimer = setInterval(function () {
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
        }
      }, PING_MS);
    };

    socket.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'play_region') {
        currentRegion = msg.region;
        onPlayRegion(msg.url, typeof msg.volume === 'number' ? msg.volume : 100, msg.region);
      } else if (msg.type === 'stop') {
        currentRegion = null;
        onPlayRegion('', 0, null);
      }
      // 'pong' needs no action.
    };

    socket.onclose = function () {
      clearInterval(pingTimer);
      setStatus('Reconnecting…', 'err');
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_CAP_MS);
    };

    socket.onerror = function () {
      // onclose handles reconnect; just close to trigger it.
      try { socket.close(); } catch (e) { /* ignore */ }
    };
  }

  // ---- boot ----------------------------------------------------------------
  A = makeWidget('widgetA');
  B = makeWidget('widgetB');
  activeWidget = A;
  idleWidget = B;

  // First gesture anywhere unlocks audio (button is the obvious target).
  const unlockOnce = function () {
    unlock();
    document.removeEventListener('click', unlockOnce);
    document.removeEventListener('keydown', unlockOnce);
    document.removeEventListener('touchstart', unlockOnce);
  };
  enterBtn.addEventListener('click', unlockOnce);
  document.addEventListener('click', unlockOnce);
  document.addEventListener('keydown', unlockOnce);
  document.addEventListener('touchstart', unlockOnce);
  banner.addEventListener('click', function () { showBanner(false); safePlay(activeWidget.widget); });

  connect();
})();
