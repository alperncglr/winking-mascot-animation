import { useEffect, useRef, useState, type CSSProperties } from "react";

type ChromaKeyVideoProps = {
  src: string;
  poster: string;
  className?: string;
  style?: CSSProperties;
  /** Son satırın yazılırken göründüğü döngü başlangıcı (sn). */
  loopFrom?: number;
  /** Buraya ulaşınca sessizce loopFrom'a atlanır (video hiç durmaz/kapanmaz). */
  loopTo?: number;
  /** true olunca video tam o anki karede donar (kayıt durdurulduğunda). */
  paused?: boolean;
};

/**
 * Excess-green eşiği: (2*g - r - b) bu değeri geçen pikseller yeşil ekran
 * sayılıp şeffaflaştırılır. Kaynak videonun yeşili farklı bir tonsa
 * (daha koyu/açık), bu iki sabiti ayarlaman yeterli.
 */
const EXG_CUTOFF = 60;
const EXG_SOFTBAND = 90;

// Kaynak video büyük çözünürlükte olabilir; ekranda zaten küçük
// gösterildiği için işleme çözünürlüğünü düşürüp performansı ciddi
// oranda artırıyoruz (piksel döngüsü boyutun karesiyle orantılı maliyetli).
const MAX_PROCESS_DIM = 480;

type VideoWithFrameCallback = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: () => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

export function ChromaKeyVideo({ src, poster, className, style, loopFrom = 5.4, loopTo = 7.1, paused = false }: ChromaKeyVideoProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [isReady, setIsReady] = useState(false);
  const hasStartedRef = useRef(false);

  useEffect(() => {
    const video = videoRef.current as VideoWithFrameCallback | null;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;

    let rafId = 0;
    let startTimer = 0;
    let stopped = false;
    let hasPainted = false;
    const useFrameCallback = typeof video.requestVideoFrameCallback === "function";

    const drawFrame = () => {
      if (stopped) return;
      const { videoWidth, videoHeight } = video;
      if (videoWidth && videoHeight && !video.paused && !video.ended) {
        const scale = Math.min(1, MAX_PROCESS_DIM / Math.max(videoWidth, videoHeight));
        const width = Math.round(videoWidth * scale);
        const height = Math.round(videoHeight * scale);
        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width;
          canvas.height = height;
        }
        ctx.drawImage(video, 0, 0, width, height);
        const frame = ctx.getImageData(0, 0, width, height);
        const data = frame.data;
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i]!;
          const g = data[i + 1]!;
          const b = data[i + 2]!;
          const exg = 2 * g - r - b;
          if (exg > EXG_CUTOFF) {
            const t = Math.min(1, (exg - EXG_CUTOFF) / EXG_SOFTBAND);
            data[i + 3] = Math.round(data[i + 3]! * (1 - t));
            const avg = (r + b) / 2;
            if (g > avg) data[i + 1] = avg;
          }
        }
        ctx.putImageData(frame, 0, 0);
        // Poster'dan canvas'a ilk kare gerçekten çizildikten sonra geç —
        // aksi halde bomboş (tamamen şeffaf) canvas bir an için görünüp
        // açılış anında "saydamlık" flaşına sebep oluyordu.
        if (!hasPainted) {
          hasPainted = true;
          setIsReady(true);
        }
      }
      if (useFrameCallback && video.requestVideoFrameCallback) {
        rafId = video.requestVideoFrameCallback(drawFrame);
      } else {
        rafId = requestAnimationFrame(drawFrame);
      }
    };

    // Video hiç bitmesin/kapanmasın: loopTo'ya ulaşınca sessizce loopFrom'a
    // atla, böylece son satırın yazılma hareketi sonsuza kadar tekrarlanır.
    const handleTimeUpdate = () => {
      if (video.currentTime >= loopTo) {
        video.currentTime = loopFrom;
      }
    };

    const beginAnimation = () => {
      video.pause();
      video.currentTime = 0;
      window.clearTimeout(startTimer);
      startTimer = window.setTimeout(() => {
        hasStartedRef.current = true;
        void video.play();
        if (useFrameCallback && video.requestVideoFrameCallback) {
          rafId = video.requestVideoFrameCallback(drawFrame);
        } else {
          rafId = requestAnimationFrame(drawFrame);
        }
      }, 180);
    };

    video.addEventListener("canplay", beginAnimation, { once: true });
    video.addEventListener("timeupdate", handleTimeUpdate);
    video.load();

    return () => {
      stopped = true;
      hasStartedRef.current = false;
      window.clearTimeout(startTimer);
      if (useFrameCallback && video.cancelVideoFrameCallback) {
        video.cancelVideoFrameCallback(rafId);
      } else {
        cancelAnimationFrame(rafId);
      }
      video.removeEventListener("canplay", beginAnimation);
      video.removeEventListener("timeupdate", handleTimeUpdate);
      video.pause();
    };
  }, [src, loopFrom, loopTo]);

  // Kayıt durdurulduğunda videoyu tam o anki karede dondur; devam edildiğinde
  // kaldığı yerden oynatmaya devam etsin (baştan başlamasın).
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !hasStartedRef.current) return;
    if (paused) {
      video.pause();
    } else if (video.paused) {
      void video.play();
    }
  }, [paused]);

  return (
    <div className={className} style={style}>
      <img
        src={poster}
        alt=""
        className={isReady ? "meeting-mascot-poster is-hidden" : "meeting-mascot-poster"}
      />
      <video ref={videoRef} src={src} muted playsInline preload="auto" className="meeting-mascot-source-video" />
      <canvas
        ref={canvasRef}
        className={isReady ? "meeting-mascot-media is-ready" : "meeting-mascot-media"}
      />
    </div>
  );
}
