(() => {
  const CFG = {
    wsPattern: "/api/arena/ws",
    attackBurst: 16,
    attackDelayMinMs: 44,
    attackDelayMaxMs: 66,
    betweenBurstMs: 120,
    positionDelayMs: 60,
    acquireTimeoutMs: 8000
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const now = () => new Date().toISOString().slice(11, 19);
  const randInt = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;

  const state = {
    stopped: false,
    running: false,
    activeSocket: null,
    mirrorSocket: null,
    latestArena: null,
    playerPos: null,
    installed: false,
    mirroredFromPerf: false
  };

  const orig = {
    WebSocket: window.WebSocket,
    send: WebSocket.prototype.send
  };

  const log = (...args) => console.log(`[pvExpert2 ${now()}]`, ...args);
  const warn = (...args) => console.warn(`[pvExpert2 ${now()}]`, ...args);

  const isArenaUrl = (url) => typeof url === "string" && url.includes(CFG.wsPattern);

  function parseNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function toPos(x, y) {
    const px = parseNumber(x);
    const py = parseNumber(y);
    return px === null || py === null ? null : { x: px, y: py };
  }

  function extractPlayerPosFromState(data) {
    const pairs = [
      ["playerTileX", "playerTileY"],
      ["playerGridX", "playerGridY"],
      ["playerX", "playerY"],
      ["gridX", "gridY"],
      ["tileX", "tileY"]
    ];

    for (const [kx, ky] of pairs) {
      const p = toPos(data?.[kx], data?.[ky]);
      if (p) return p;
    }

    const nested = [data?.player, data?.position, data?.character];
    for (const obj of nested) {
      if (!obj || typeof obj !== "object") continue;
      const p1 = toPos(obj.tileX, obj.tileY);
      const p2 = toPos(obj.gridX, obj.gridY);
      const p3 = toPos(obj.x, obj.y);
      if (p1) return p1;
      if (p2) return p2;
      if (p3) return p3;
    }

    return null;
  }

  function updatePlayerPosFromState(data) {
    const pos = extractPlayerPosFromState(data);
    if (pos) state.playerPos = pos;
  }

  function parseIncoming(raw) {
    try {
      const data = typeof raw === "string" ? JSON.parse(raw) : JSON.parse(String(raw));
      if (data && Array.isArray(data.monsters)) {
        state.latestArena = data;
        updatePlayerPosFromState(data);
        runLoop();
      }
    } catch {
      // ignore non-json frames
    }
  }

  function attachArenaSocket(ws, source) {
    if (!ws || ws.__pvExpertAttached) return;
    ws.__pvExpertAttached = true;
    state.activeSocket = ws;

    ws.addEventListener("message", (ev) => parseIncoming(ev.data));
    ws.addEventListener("close", () => {
      if (state.activeSocket === ws) state.activeSocket = null;
    });

    log(`Arena socket attached from ${source}:`, ws.url);
  }

  function installHooks() {
    if (state.installed) return;
    state.installed = true;

    WebSocket.prototype.send = function patchedSend(data) {
      try {
        if (isArenaUrl(this.url)) {
          attachArenaSocket(this, "prototype.send");
        }

        if (typeof data === "string") {
          const parsed = JSON.parse(data);
          if (parsed?.type === "position") {
            const p = toPos(parsed.gridX, parsed.gridY);
            if (p) state.playerPos = p;
          }
        }
      } catch {}
      return orig.send.call(this, data);
    };

    const NativeWS = window.WebSocket;
    function PatchedWebSocket(url, protocols) {
      const ws = protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
      try {
        if (isArenaUrl(ws.url || url)) {
          attachArenaSocket(ws, "constructor");
        }
      } catch {}
      return ws;
    }

    PatchedWebSocket.prototype = NativeWS.prototype;
    Object.setPrototypeOf(PatchedWebSocket, NativeWS);
    window.WebSocket = PatchedWebSocket;

    log("Hooks installed. Mencari socket arena aktif...");
  }

  function findArenaUrlFromPerformance() {
    const entries = performance.getEntriesByType("resource") || [];
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      const name = entries[i]?.name;
      if (isArenaUrl(name) && name.startsWith("wss://")) return name;
    }
    return null;
  }

  function tryCreateMirrorSocket() {
    if (state.activeSocket) return;
    const url = findArenaUrlFromPerformance();
    if (!url) return;
    if (state.mirrorSocket && state.mirrorSocket.readyState <= 1) return;

    try {
      const ws = new orig.WebSocket(url);
      state.mirrorSocket = ws;
      state.mirroredFromPerf = true;
      ws.addEventListener("open", () => attachArenaSocket(ws, "performance-url mirror"));
      ws.addEventListener("message", (ev) => parseIncoming(ev.data));
      ws.addEventListener("error", () => warn("Mirror socket error"));
      ws.addEventListener("close", () => {
        if (state.activeSocket === ws) state.activeSocket = null;
      });
      log("Membuat mirror socket dari performance entry.");
    } catch (e) {
      warn("Gagal membuat mirror socket:", e);
    }
  }

  function getAliveMonsters() {
    return (state.latestArena?.monsters ?? [])
      .map((m, i) => ({ index: i, m }))
      .filter(({ m }) => Number(m.hp) > 0);
  }

  function distanceToPlayer(monster) {
    if (!state.playerPos) return Number.POSITIVE_INFINITY;
    const mx = parseNumber(monster?.tileX);
    const my = parseNumber(monster?.tileY);
    if (mx === null || my === null) return Number.POSITIVE_INFINITY;
    return Math.abs(mx - state.playerPos.x) + Math.abs(my - state.playerPos.y);
  }

  function pickNearestAliveMonster() {
    const alive = getAliveMonsters();
    if (!alive.length) return null;
    if (!state.playerPos) return alive[0];

    alive.sort((a, b) => {
      const da = distanceToPlayer(a.m);
      const db = distanceToPlayer(b.m);
      return da - db;
    });
    return alive[0];
  }

  function send(obj) {
    const ws = state.activeSocket;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(obj));
    if (obj?.type === "position") {
      const p = toPos(obj.gridX, obj.gridY);
      if (p) state.playerPos = p;
    }
    return true;
  }

  async function acquireSocket() {
    const start = Date.now();
    while (!state.stopped && Date.now() - start < CFG.acquireTimeoutMs) {
      if (state.activeSocket && state.activeSocket.readyState === WebSocket.OPEN) return true;
      tryCreateMirrorSocket();
      await sleep(250);
    }
    return false;
  }

  async function runLoop() {
    if (state.running || state.stopped) return;
    state.running = true;

    try {
      const ok = await acquireSocket();
      if (!ok) {
        warn("Socket arena belum terdeteksi. Coba gerakkan karakter / lakukan 1 aksi di game.");
        return;
      }

      while (!state.stopped) {
        const nearest = pickNearestAliveMonster();
        if (!nearest) {
          await sleep(120);
          continue;
        }

        const targetIndex = nearest.index;
        const initialDist = distanceToPlayer(nearest.m);
        log("Target nearest monster:", targetIndex, "distance:", initialDist);

        while (!state.stopped) {
          const target = state.latestArena?.monsters?.[targetIndex];
          if (!target || Number(target.hp) <= 0) {
            log("Monster mati:", targetIndex);
            break;
          }

          if (!send({ type: "position", gridX: target.tileX, gridY: target.tileY })) {
            warn("Socket tidak open saat kirim position.");
            await sleep(300);
            continue;
          }
          await sleep(CFG.positionDelayMs);

          for (let n = 0; n < CFG.attackBurst; n += 1) {
            if (state.stopped) break;
            const check = state.latestArena?.monsters?.[targetIndex];
            if (!check || Number(check.hp) <= 0) break;
            if (!send({ type: "attack", monsterIndex: targetIndex })) break;
            await sleep(randInt(CFG.attackDelayMinMs, CFG.attackDelayMaxMs));
          }

          await sleep(CFG.betweenBurstMs);
        }
      }
    } finally {
      state.running = false;
    }
  }

  function stop(closeMirror = false) {
    state.stopped = true;
    if (closeMirror && state.mirrorSocket && state.mirrorSocket.readyState <= 1) {
      try { state.mirrorSocket.close(); } catch {}
    }
    log("Stopped.");
  }

  function resume() {
    if (!state.stopped) return;
    state.stopped = false;
    runLoop();
  }

  installHooks();
  runLoop();

  window.pvExpert2 = {
    stop,
    resume,
    send,
    state: () => state.latestArena,
    status: () => ({
      running: state.running,
      stopped: state.stopped,
      playerPos: state.playerPos,
      activeSocketUrl: state.activeSocket?.url ?? null,
      activeSocketReadyState: state.activeSocket?.readyState ?? null,
      mirroredFromPerf: state.mirroredFromPerf
    })
  };

  log("Ready. Commands: pvExpert2.status(), pvExpert2.stop(), pvExpert2.resume()");
})();
