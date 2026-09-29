// Mikrofon sesini yakalayıp 16kHz mono PCM16'ya çevirip WebSocket üzerinden
// backend'e akıtır. Mantık, backend'in kendi kanıtlanmış test arayüzündeki
// (meeting_scribe/server/templates/index.html) downsampleTo16k akışının
// birebir TypeScript karşılığıdır.
//
// Mikrofon toplantı masasının ortasında olacağından uzaktaki katılımcılar
// zor duyulabilir; bunu telafi etmek için source ile scriptNode arasına
// bir kompresör + kazanç zinciri eklendi (aşağıdaki createGainStage).

export interface LiveSegmentEvent {
  segment_id: string;
  revision: number;
  status: "provisional" | "final";
  speaker_label: string;
  text: string;
}

interface LiveAudioStreamCallbacks {
  onSegment: (event: LiveSegmentEvent) => void;
  onError?: (message: string) => void;
  onClose?: () => void;
  onStatus?: (status: LiveAudioStatus) => void;
}

export interface LiveAudioStatus {
  connection: "connecting" | "connected" | "closed";
  microphone: "starting" | "listening" | "muted" | "stalled" | "stopped";
  delivery: "waiting" | "confirmed" | "delayed";
}

function downsampleTo16k(input: Float32Array, inputRate: number): Float32Array {
  const outputRate = 16000;
  if (inputRate === outputRate) return input;
  if (inputRate < outputRate) throw new Error("Mikrofon örnekleme hızı 16 kHz altına düşemez.");
  const ratio = inputRate / outputRate;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let outIndex = 0; outIndex < output.length; outIndex++) {
    const start = Math.floor(outIndex * ratio);
    const end = Math.max(start + 1, Math.floor((outIndex + 1) * ratio));
    let sum = 0;
    for (let inIndex = start; inIndex < end && inIndex < input.length; inIndex++) {
      sum += input[inIndex]!;
    }
    output[outIndex] = sum / (end - start);
  }
  return output;
}

// Uzak konuşmacıları güçlendirmek için highpass filtre + kompresör + sabit
// kazanç zinciri kurar:
//   - highpass: konuşmanın altındaki oda uğultusunu/hum'u kompresöre
//     girmeden önce keser (kompresör aksi halde gürültüyü de yükseltirdi).
//   - compressor: yüksek sesleri bastırıp ortalamayı yükseltir (AGC etkisi).
//   - gain: kompresörden sonra genel seviyeyi sabitçe yukarı çeker.
// Zincir: source -> highpass -> compressor -> gain -> scriptNode.
function createGainStage(
  context: AudioContext,
  source: MediaStreamAudioSourceNode,
): AudioNode {
  const highpass = context.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = 100;

  const compressor = context.createDynamicsCompressor();
  compressor.threshold.value = -50;
  compressor.knee.value = 30;
  compressor.ratio.value = 12;
  compressor.attack.value = 0.003;
  compressor.release.value = 0.25;

  const gain = context.createGain();
  gain.gain.value = 2.5;

  source.connect(highpass);
  highpass.connect(compressor);
  compressor.connect(gain);
  return gain;
}

export class LiveAudioStream {
  private socket: WebSocket | null = null;
  private audioContext: AudioContext | null = null;
  private micStream: MediaStream | null = null;
  private scriptNode: ScriptProcessorNode | null = null;
  private statusTimer: ReturnType<typeof setInterval> | null = null;
  private intentionalStop = false;
  private paused = false;
  private captureGeneration = 0;
  private lastAudioAt = 0;
  private lastAckAt = 0;
  private startedAt = 0;
  private lastPublishedStatus = "";
  private callbacks: LiveAudioStreamCallbacks | null = null;

  private publishStatus(): void {
    const track = this.micStream?.getAudioTracks()[0];
    const now = Date.now();
    const status: LiveAudioStatus = {
      connection: this.socket?.readyState === WebSocket.OPEN ? "connected" : this.socket?.readyState === WebSocket.CONNECTING ? "connecting" : "closed",
      microphone: this.paused ? "stopped" : !track || !this.lastAudioAt ? "starting" : track.readyState === "ended" ? "stopped" : track.muted ? "muted" : this.audioContext?.state !== "running" || now - this.lastAudioAt > 3000 ? "stalled" : "listening",
      delivery: this.paused ? "waiting" : this.lastAckAt && now - this.lastAckAt < 5000 ? "confirmed" : now - this.startedAt > 5000 ? "delayed" : "waiting",
    };
    const encoded = JSON.stringify(status);
    if (encoded !== this.lastPublishedStatus) {
      this.callbacks?.onStatus?.(status);
      this.lastPublishedStatus = encoded;
    }
  }

  async start(wsUrl: string, callbacks: LiveAudioStreamCallbacks): Promise<void> {
    this.callbacks = callbacks;
    this.intentionalStop = false;
    this.paused = false;
    this.startedAt = Date.now();
    this.lastAudioAt = 0;
    this.lastAckAt = 0;
    this.lastPublishedStatus = "";
    if (!window.isSecureContext && location.hostname !== "localhost") {
      throw new Error("Mikrofon erişimi için arayüz HTTPS üzerinden açılmalıdır.");
    }

    this.socket = new WebSocket(wsUrl);
    this.statusTimer = setInterval(() => this.publishStatus(), 1000);
    this.publishStatus();

    this.socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "audio_status") { this.lastAckAt = Date.now(); this.publishStatus(); return; }
        if (typeof payload.segment_id === "string" && typeof payload.text === "string") callbacks.onSegment(payload as LiveSegmentEvent);
      } catch {
        // Beklenmeyen mesaj formatı — sessizce atlanır.
      }
    };
    this.socket.onerror = () => callbacks.onError?.("Canlı bağlantı hatası.");
    this.socket.onclose = (event) => {
      const intentional = this.intentionalStop;
      this.socket = null;
      this.captureGeneration += 1;
      this.releaseAudio();
      callbacks.onStatus?.({ connection: "closed", microphone: "stopped", delivery: "waiting" });
      if (!intentional) callbacks.onError?.(`Canlı bağlantı kesildi (kod ${event.code}). Ses sunucuya gönderilmiyor. Toplantıyı bitirip kaydedilen sesi döküme alabilirsiniz.`);
      callbacks.onClose?.();
    };

    await new Promise<void>((resolve, reject) => {
      if (!this.socket) return reject(new Error("Soket oluşturulamadı."));
      this.socket.onopen = () => resolve();
      this.socket.addEventListener("error", () => reject(new Error("WebSocket açılamadı.")), { once: true });
      this.socket.addEventListener("close", () => reject(new Error("Canlı bağlantı açılmadan kapandı.")), { once: true });
    });

    if (!this.paused && !this.intentionalStop) await this.startMicrophone();
  }

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  pause(): void {
    this.paused = true;
    this.captureGeneration += 1;
    this.releaseMicrophone();
    this.publishStatus();
  }

  async resume(): Promise<void> {
    if (!this.isConnected()) throw new Error("Canlı bağlantı kapandı; yeniden bağlanmak gerekiyor.");
    if (!this.paused) return;
    this.paused = false;
    this.lastAudioAt = 0;
    this.publishStatus();
    await this.startMicrophone();
  }

  private async startMicrophone(): Promise<void> {
    const generation = ++this.captureGeneration;
    let micStream: MediaStream;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          noiseSuppression: true,
          echoCancellation: true,
          // Kendi kompresör/kazanç zincirimiz olduğundan tarayıcının AGC'si
          // kapatılıyor; ikisi birden gürültüyü de aşırı yükseltebilir.
          autoGainControl: false,
        },
      });
    } catch (error) {
      if (generation !== this.captureGeneration || this.paused || this.intentionalStop) return;
      throw new Error(error instanceof Error ? error.message : "Mikrofon başlatılamadı.");
    }

    // A pause or a newer resume can overtake the browser permission prompt.
    if (generation !== this.captureGeneration || this.paused || this.intentionalStop) {
      micStream.getTracks().forEach((track) => track.stop());
      return;
    }

    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      micStream.getTracks().forEach((track) => track.stop());
      throw new Error("Mikrofon açılırken bağlantı kesildi.");
    }

    let audioContext: AudioContext;
    try {
      audioContext = new AudioContext();
    } catch (error) {
      micStream.getTracks().forEach((track) => track.stop());
      throw error;
    }
    try {
      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }
    } catch (error) {
      micStream.getTracks().forEach((track) => track.stop());
      void audioContext.close();
      if (generation !== this.captureGeneration || this.paused || this.intentionalStop) return;
      throw error;
    }
    if (generation !== this.captureGeneration || this.paused || this.intentionalStop) {
      micStream.getTracks().forEach((track) => track.stop());
      void audioContext.close();
      return;
    }
    if (audioContext.state !== "running") {
      micStream.getTracks().forEach((track) => track.stop());
      void audioContext.close();
      throw new Error(
        "Tarayıcı ses işlemeyi duraklattı. Sayfaya tıklayıp toplantıyı yeniden başlatın.",
      );
    }
    this.micStream = micStream;
    this.audioContext = audioContext;
    const source = audioContext.createMediaStreamSource(micStream);
    const boosted = createGainStage(audioContext, source);
    this.scriptNode = audioContext.createScriptProcessor(4096, 1, 1);
    boosted.connect(this.scriptNode);
    this.scriptNode.connect(audioContext.destination);

    this.scriptNode.onaudioprocess = (event) => {
      if (this.paused || generation !== this.captureGeneration) return;
      this.lastAudioAt = Date.now();
      const input = downsampleTo16k(event.inputBuffer.getChannelData(0), audioContext.sampleRate);
      const pcm16 = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        const clamped = Math.max(-1, Math.min(1, input[i]!));
        pcm16[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      }
      const socket = this.socket;
      if (socket && socket.readyState === WebSocket.OPEN) {
        if (socket.bufferedAmount < 1024 * 1024) {
          socket.send(pcm16.buffer);
        } else {
          this.callbacks?.onError?.("Ağ ses akışına yetişemiyor; bağlantıyı kontrol edin.");
        }
      }
    };
    this.publishStatus();
  }

  private releaseMicrophone(): void {
    if (this.scriptNode) {
      this.scriptNode.disconnect();
      this.scriptNode = null;
    }
    if (this.audioContext) {
      void this.audioContext.close();
      this.audioContext = null;
    }
    if (this.micStream) {
      this.micStream.getTracks().forEach((track) => track.stop());
      this.micStream = null;
    }
  }

  private releaseAudio(): void {
    if (this.statusTimer) { clearInterval(this.statusTimer); this.statusTimer = null; }
    this.releaseMicrophone();
  }

  stop(): void {
    this.intentionalStop = true;
    this.paused = false;
    this.captureGeneration += 1;
    this.releaseAudio();
    this.callbacks?.onStatus?.({ connection: "closed", microphone: "stopped", delivery: "waiting" });
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }
}
