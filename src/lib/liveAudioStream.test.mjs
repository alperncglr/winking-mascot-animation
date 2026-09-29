import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

test("pausing and resuming keeps the same live WebSocket", async () => {
  const source = await readFile(new URL("./liveAudioStream.ts", import.meta.url), "utf8");
  const javascript = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { LiveAudioStream } = await import(`data:text/javascript,${encodeURIComponent(javascript)}`);
  const sockets = [];
  const tracks = [];
  const node = () => ({ connect() {}, disconnect() {} });

  class MockSocket extends EventTarget {
    static OPEN = 1;
    static CONNECTING = 0;
    readyState = MockSocket.CONNECTING;
    bufferedAmount = 0;
    constructor() {
      super();
      sockets.push(this);
      queueMicrotask(() => { this.readyState = MockSocket.OPEN; this.onopen?.(); });
    }
    close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
  }
  class MockAudioContext {
    state = "running";
    destination = node();
    createMediaStreamSource() { return node(); }
    createBiquadFilter() { return { ...node(), frequency: {}, type: "" }; }
    createDynamicsCompressor() {
      return { ...node(), threshold: {}, knee: {}, ratio: {}, attack: {}, release: {} };
    }
    createGain() { return { ...node(), gain: {} }; }
    createScriptProcessor() { return { ...node(), onaudioprocess: null }; }
    close() { this.state = "closed"; return Promise.resolve(); }
  }
  const previous = Object.fromEntries(
    ["window", "location", "navigator", "WebSocket", "AudioContext"]
      .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]),
  );
  const mocks = {
    window: { isSecureContext: true }, location: { hostname: "localhost" },
    navigator: { mediaDevices: { async getUserMedia() {
      const track = { readyState: "live", muted: false, stop() { this.readyState = "ended"; } };
      tracks.push(track);
      return { getTracks: () => [track], getAudioTracks: () => [track] };
    } } },
    WebSocket: MockSocket, AudioContext: MockAudioContext,
  };
  for (const [key, value] of Object.entries(mocks)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }

  const stream = new LiveAudioStream();
  try {
    await stream.start("ws://localhost/live", { onSegment() {} });
    assert.equal(sockets.length, 1);
    assert.equal(tracks.length, 1);
    stream.pause();
    assert.equal(tracks[0].readyState, "ended");
    assert.equal(stream.isConnected(), true);
    await stream.resume();
    assert.equal(sockets.length, 1);
    assert.equal(tracks.length, 2);
    assert.equal(tracks[1].readyState, "live");
  } finally {
    stream.stop();
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
