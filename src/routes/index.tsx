import { createFileRoute } from "@tanstack/react-router";
import { Check, CircleStop, Download, FileText, Loader2, Moon, Pause, Play, RotateCcw, Sun } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { ChromaKeyVideo } from "@/components/chroma-key-video";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import sittingMascotBody from "@/assets/deft3r-mascot-sitting-body.png";
import sittingLegLeftUpper from "@/assets/deft3r-mascot-sitting-leg-left-upper.png";
import sittingLegLeftLower from "@/assets/deft3r-mascot-sitting-leg-left-lower.png";
import sittingLegRightUpper from "@/assets/deft3r-mascot-sitting-leg-right-upper.png";
import sittingLegRightLower from "@/assets/deft3r-mascot-sitting-leg-right-lower.png";

const mascot = "/media/deft3r-notebook-mascot.png";
const sleepingMascot = "/media/deft3r-mascot-sleeping.png";
const blinkMascot = "/media/deft3r-mascot-blink.png";
const writingVideo = "/media/deft3r-open-and-continuous-writing.mp4";


export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "DEFT3R — Akıllı Toplantı Defteri" },
      { name: "description", content: "Toplantıları gerçek zamanlı yazıya döken ve özetleyen sevimli dijital defter." },
      { property: "og:title", content: "DEFT3R — Akıllı Toplantı Defteri" },
      { property: "og:description", content: "Toplantıları gerçek zamanlı yazıya döken ve özetleyen sevimli dijital defter." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

type AppState = "ready" | "meeting" | "closing" | "summary";
type DocStatus = "idle" | "working" | "done";
type IntroPlacement = CSSProperties & {
  "--intro-target-left": string;
  "--intro-target-top": string;
  "--intro-target-width": string;
  "--intro-target-height": string;
  "--intro-start-x": string;
  "--intro-start-y": string;
  "--intro-start-scale": string;
  "--intro-apex-x": string;
  "--intro-apex-y": string;
  "--intro-logo-top": string;
  "--intro-sitting-left": string;
  "--intro-sitting-top": string;
  "--intro-sitting-size": string;
  "--intro-sit-dx": string;
  "--intro-sit-dy": string;
  "--intro-sit-scale": string;
  "--intro-sit-apex-x": string;
  "--intro-sit-apex-y": string;
  "--intro-sit-arc-y": string;
  "--intro-logo-dx": string;
  "--intro-logo-dy": string;
  "--intro-logo-scale": string;
  "--intro-name-dx": string;
  "--intro-name-dy": string;
  "--intro-name-scale": string;
  "--intro-assistant-dx": string;
  "--intro-assistant-dy": string;
  "--intro-assistant-scale": string;
  "--intro-powered-dx": string;
  "--intro-powered-dy": string;
  "--intro-shadow-left": string;
  "--intro-shadow-top": string;
  "--intro-shadow-width": string;
  "--intro-shadow-height": string;
};

const conversation = [
  { name: "Ayşe", initials: "AY", tone: "coral", time: "00:08", text: "Günaydın! Önce bu haftanın önceliklerini netleştirelim." },
  { name: "Mert", initials: "ME", tone: "blue", time: "00:16", text: "Kullanıcı testlerini perşembeye kadar tamamlayabiliriz." },
  { name: "Selin", initials: "SE", tone: "yellow", time: "00:25", text: "Harika, ben de bulguları cuma sabahı ekiple paylaşırım." },
  { name: "Ayşe", initials: "AY", tone: "coral", time: "00:34", text: "O zaman bu haftanın ana hedefi kullanıcı testleri olsun." },
];

function Index() {
  const [state, setState] = useState<AppState>("ready");
  const [title, setTitle] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [visibleMessages, setVisibleMessages] = useState(0);
  const [notice, setNotice] = useState("");
  const [summaryStatus, setSummaryStatus] = useState<DocStatus>("idle");
  const [transcriptStatus, setTranscriptStatus] = useState<DocStatus>("idle");
  const [travelStartTop, setTravelStartTop] = useState<number | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [isDark, setIsDark] = useState(false);
  const [introVisible, setIntroVisible] = useState(true);
  const [introReady, setIntroReady] = useState(false);
  const [introStarted, setIntroStarted] = useState(false);
  const [introPlacement, setIntroPlacement] = useState<IntroPlacement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const readyMascotRef = useRef<HTMLImageElement>(null);
  const readyShadowRef = useRef<HTMLSpanElement>(null);
  const headerLogoRef = useRef<HTMLImageElement>(null);
  const headerNameRef = useRef<HTMLParagraphElement>(null);
  const headerAssistantRef = useRef<HTMLParagraphElement>(null);
  const openingLogoRef = useRef<HTMLImageElement>(null);
  const openingNameRef = useRef<HTMLParagraphElement>(null);
  const openingAssistantRef = useRef<HTMLParagraphElement>(null);
  const openingPoweredRef = useRef<HTMLParagraphElement>(null);

  useLayoutEffect(() => {
    const placeIntro = () => {
      const target = readyMascotRef.current?.getBoundingClientRect();
      const targetShadow = readyShadowRef.current?.getBoundingClientRect();
      if (!target || !targetShadow || target.width === 0) return;

      const startWidth = Math.min(window.innerWidth * 0.34, 170);
      const startLeft = (window.innerWidth - startWidth) / 2;
      const startTop = Math.max(18, window.innerHeight * 0.055);
      const startScale = startWidth / target.width;
      const footX = target.width * (650 / 1024);
      const footY = target.height * (938 / 1024);
      const startX = startLeft - target.left - footX * (1 - startScale);
      const startY = startTop - target.top - footY * (1 - startScale);

      // Oturma çizgisi gövdenin düz alt kenarıdır (760/1024); aşağıdaki alfa
      // pikselleri bacaklara aittir ve temas hesabına dahil edilmez.
      const logoTop = startTop + startWidth * (760 / 1024) - 1;
      const sitDx = target.left - startLeft;
      const sitDy = target.top - startTop;

      const sitApexY = -Math.max(24, Math.min(90, startTop * 0.55));

      setIntroPlacement({
        "--intro-logo-dx": "0px",
        "--intro-logo-dy": "0px",
        "--intro-logo-scale": "1",
        "--intro-name-dx": "0px",
        "--intro-name-dy": "0px",
        "--intro-name-scale": "1",
        "--intro-assistant-dx": "0px",
        "--intro-assistant-dy": "0px",
        "--intro-assistant-scale": "1",
        "--intro-powered-dx": "0px",
        "--intro-powered-dy": "0px",
        "--intro-shadow-left": `${targetShadow.left}px`,
        "--intro-shadow-top": `${targetShadow.top}px`,
        "--intro-shadow-width": `${targetShadow.width}px`,
        "--intro-shadow-height": `${targetShadow.height}px`,
        "--intro-sit-dx": `${sitDx}px`,
        "--intro-sit-dy": `${sitDy}px`,
        "--intro-sit-scale": `${target.width / startWidth}`,
        "--intro-sit-apex-x": `${sitDx * 0.5}px`,
        "--intro-sit-apex-y": `${sitApexY}px`,
        "--intro-sit-arc-y": `${sitApexY - sitDy * (16 / 34)}px`,
        "--intro-target-left": `${target.left}px`,
        "--intro-target-top": `${target.top}px`,
        "--intro-target-width": `${target.width}px`,
        "--intro-target-height": `${target.height}px`,
        "--intro-start-x": `${startX}px`,
        "--intro-start-y": `${startY}px`,
        "--intro-start-scale": `${startScale}`,
        "--intro-apex-x": `${startX * 0.52}px`,
        "--intro-apex-y": `${Math.min(startY, 0) - Math.min(120, window.innerHeight * 0.13)}px`,
        "--intro-logo-top": `${logoTop}px`,
        "--intro-sitting-left": `${startLeft}px`,
        "--intro-sitting-top": `${startTop}px`,
        "--intro-sitting-size": `${startWidth}px`,
      });

    };

    const frame = window.requestAnimationFrame(placeIntro);
    window.addEventListener("resize", placeIntro);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", placeIntro);
    };
  }, []);

  useLayoutEffect(() => {
    if (!introPlacement || !introVisible || introReady) return;

    const frame = window.requestAnimationFrame(() => {
      const sourceLogo = openingLogoRef.current?.getBoundingClientRect();
      const sourceName = openingNameRef.current?.getBoundingClientRect();
      const sourceAssistant = openingAssistantRef.current?.getBoundingClientRect();
      const sourcePowered = openingPoweredRef.current?.getBoundingClientRect();
      const targetLogo = headerLogoRef.current?.getBoundingClientRect();
      const targetName = headerNameRef.current?.getBoundingClientRect();
      const targetAssistant = headerAssistantRef.current?.getBoundingClientRect();

      if (!sourceLogo || !sourceName || !sourceAssistant || !sourcePowered || !targetLogo || !targetName || !targetAssistant) return;

      const delta = (source: DOMRect, target: DOMRect) => ({
        x: target.left - source.left,
        y: target.top - source.top,
        scale: target.width / source.width,
      });
      const logo = delta(sourceLogo, targetLogo);
      // DEFT3R yazısı ve "Toplantı asistanı" etiketi, açılış ekranında da
      // header'daki gerçek boyutlarıyla (paylaşılan CSS kuralı sayesinde)
      // birebir aynı font-size'da render ediliyor — bu yüzden scale'i
      // ölçülen (piksel gürültüsü içerebilen) orana değil, sabit 1'e
      // sabitliyoruz; sadece konum (dx/dy) değişiyor. Aksi halde metne
      // uygulanan ufak bir scale, tam geçiş anında bulanıklaşmaya/"büyük
      // kalma" hissine sebep oluyordu.
      const name = { ...delta(sourceName, targetName), scale: 1 };
      const assistant = { ...delta(sourceAssistant, targetAssistant), scale: 1 };
      const powered = delta(sourcePowered, targetAssistant);

      setIntroPlacement((current) => current && ({
        ...current,
        "--intro-logo-dx": `${logo.x}px`,
        "--intro-logo-dy": `${logo.y}px`,
        "--intro-logo-scale": `${logo.scale}`,
        "--intro-name-dx": `${name.x}px`,
        "--intro-name-dy": `${name.y}px`,
        "--intro-name-scale": `${name.scale}`,
        "--intro-assistant-dx": `${assistant.x}px`,
        "--intro-assistant-dy": `${assistant.y}px`,
        "--intro-assistant-scale": `${assistant.scale}`,
        "--intro-powered-dx": `${powered.x}px`,
        "--intro-powered-dy": `${powered.y}px`,
      }));
      setIntroReady(true);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [introPlacement, introReady, introVisible]);

  useEffect(() => {
    // Zıplama animasyonu ancak kullanıcı "Başla" düğmesine bastıktan sonra
    // oynuyor; bu yüzden emniyet zaman aşımı da o andan itibaren sayılmalı.
    if (!introStarted) return;
    const fallback = window.setTimeout(() => setIntroVisible(false), 6400);
    return () => window.clearTimeout(fallback);
  }, [introStarted]);

  // Gece modu tercihi tarayıcıda saklanıyor; sayfa ilk açılışta __root'taki
  // betikle uygulanıyor, burada yalnızca düğmenin ikonu senkronize ediliyor.
  useEffect(() => {
    setIsDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggleTheme(event: React.MouseEvent<HTMLButtonElement>) {
    const next = !isDark;
    const applyTheme = () => {
      document.documentElement.classList.toggle("dark", next);
      try {
        localStorage.setItem("deft3r-theme", next ? "dark" : "light");
      } catch {
        // localStorage kapalıysa seçim yalnızca bu oturum için geçerli kalır.
      }
      setIsDark(next);
    };

    // Switch'in tıklandığı noktadan büyüyen bir daire ile geçiş yapılıyor;
    // View Transitions API'yi desteklemeyen tarayıcılarda ya da azaltılmış
    // hareket tercih edildiğinde animasyonsuz, anlık geçiş yapılıyor.
    // Not: API, geçiş boyunca tüm sayfayı statik bir ekran görüntüsü olarak
    // dondurduğu için toplantı ekranındaki canlı video da bu ~650ms boyunca
    // kısa bir an duraklamış görünür — bu, tekniğin doğasında olan kabul
    // edilebilir bir durum.
    const supportsViewTransition = typeof document.startViewTransition === "function";
    if (!supportsViewTransition || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      applyTheme();
      return;
    }

    const { clientX, clientY } = event;
    const maxRadius = Math.hypot(
      Math.max(clientX, window.innerWidth - clientX),
      Math.max(clientY, window.innerHeight - clientY),
    );

    const transition = document.startViewTransition(() => {
      applyTheme();
    });

    transition.ready.then(() => {
      // Daire dümdüz (tam bir çember olarak) büyüyor — şekli hiç bozulmuyor.
      // "Deniz dalgası" hissi, kırpılan şeklin görünen kenarını saran bir
      // drop-shadow parıltısıyla veriliyor: parıltı ortada en güçlü, başta
      // ve sonda sıfır, yani dalga cephesi dairenin kendisiyle birlikte
      // ilerliyormuş gibi görünüyor.
      document.documentElement.animate(
        [
          {
            clipPath: `circle(0px at ${clientX}px ${clientY}px)`,
            filter: "drop-shadow(0 0 0px oklch(1 0 0 / 0)) drop-shadow(0 0 0px oklch(0.4 0.05 60 / 0))",
          },
          {
            clipPath: `circle(${maxRadius * 0.55}px at ${clientX}px ${clientY}px)`,
            filter: "drop-shadow(0 0 18px oklch(1 0 0 / .55)) drop-shadow(0 0 5px oklch(0.4 0.05 60 / .35))",
            offset: 0.55,
          },
          {
            clipPath: `circle(${maxRadius}px at ${clientX}px ${clientY}px)`,
            filter: "drop-shadow(0 0 0px oklch(1 0 0 / 0)) drop-shadow(0 0 0px oklch(0.4 0.05 60 / 0))",
          },
        ],
        {
          duration: 650,
          easing: "ease-in-out",
          pseudoElement: "::view-transition-new(root)",
        },
      );
    });
  }

  // Toplantı videosunu, kullanıcı "Toplantıyı Başlat"a basmadan önce arka
  // planda önceden yükle — aksi halde ilk tıklamada video ağdan inene kadar
  // kısa bir boşluk oluşuyor.
  useEffect(() => {
    const preloadVideo = document.createElement("video");
    preloadVideo.preload = "auto";
    preloadVideo.src = writingVideo;
    preloadVideo.load();
  }, []);

  useEffect(() => {
    if (state !== "meeting" || isPaused) return;
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    const bubbles = window.setInterval(
      () => setVisibleMessages((value) => Math.min(value + 1, conversation.length)),
      1900,
    );
    return () => {
      window.clearInterval(timer);
      window.clearInterval(bubbles);
    };
  }, [state, isPaused]);

  useEffect(() => () => streamRef.current?.getTracks().forEach((track) => track.stop()), []);

  async function startMeeting() {
    setNotice("");
    const rect = readyMascotRef.current?.getBoundingClientRect();
    setTravelStartTop(rect ? rect.top : null);
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setNotice("Mikrofon izni alınamadı. Demo sessiz modda devam ediyor.");
    }
    setSeconds(0);
    setVisibleMessages(1);
    setIsPaused(false);
    setState("meeting");
  }

  function endMeeting() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setVisibleMessages(conversation.length);
    setIsPaused(false);
    setState("summary");
  }


  function reset() {
    setState("ready");
    setSeconds(0);
    setVisibleMessages(0);
    setNotice("");
    setSummaryStatus("idle");
    setTranscriptStatus("idle");
    setIsPaused(false);
  }

  const minutesLabel = String(Math.floor(seconds / 60)).padStart(2, "0");
  const secondsLabel = String(seconds % 60).padStart(2, "0");
  const time = `${minutesLabel}:${secondsLabel}`;
  const meetingName = title.trim() || "İsimsiz toplantı";

  function download(fileName: string, content: string) {
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function slug() {
    return meetingName.toLocaleLowerCase("tr").replace(/[^a-z0-9ğüşiöç]+/gi, "-").replace(/^-|-$/g, "") || "toplanti";
  }

  function prepareSummary() {
    if (summaryStatus === "working") return;
    setSummaryStatus("working");
    window.setTimeout(() => {
      download(
        `${slug()}-ozet.txt`,
        [
          `${meetingName} — Toplantı Özeti`,
          `Süre: ${time}`,
          "",
          "Özet:",
          "Ekip, bu haftanın ana odağını kullanıcı testleri olarak belirledi. Test sonuçları cuma sabahı paylaşılacak.",
          "",
          "Yapılacaklar:",
          "- Kullanıcı testlerini tamamla",
          "- Bulguları ekiple paylaş",
        ].join("\n"),
      );
      setSummaryStatus("done");
    }, 2200);
  }

  function prepareTranscript() {
    if (transcriptStatus === "working") return;
    setTranscriptStatus("working");
    window.setTimeout(() => {
      download(
        `${slug()}-transkript.txt`,
        [`${meetingName} — Toplantı Transkripti`, `Süre: ${time}`, "", ...conversation.map((m) => `[${m.time}] ${m.name}: ${m.text}`)].join("\n"),
      );
      setTranscriptStatus("done");
    }, 2200);
  }

  return (
    <main className={cn("relative h-screen overflow-hidden bg-background text-foreground", introVisible && "intro-active", introVisible && introStarted && "intro-ready")}>
      <div aria-hidden="true" className="paper-surface absolute inset-0" />
      <div aria-hidden="true" className="paper-grid absolute inset-0" />
      <header className={cn("app-header relative z-20 mx-auto flex w-full max-w-6xl items-center justify-end px-5 py-5 sm:px-8", state !== "meeting" && "brand-hero")}>
        <button className="brand-badge" onClick={reset} aria-label="DEFT3R başlangıç ekranı">
          <span className="brand-badge-mascot">
            <span className="mascot-look block h-full w-full">
              <img src={mascot} alt="" width={1024} height={1024} className="block h-full w-full object-contain" />
              <img src={blinkMascot} alt="" aria-hidden="true" width={1024} height={1024} className="mascot-blink absolute inset-0 h-full w-full object-contain" />
            </span>
          </span>
          <img ref={headerLogoRef} src="/media/teb-ai-mark.png" alt="TEB AI logosu" className="brand-badge-teb object-contain" />
          <div className="brand-badge-text text-left leading-none">
            <p ref={headerNameRef} className="brand-wordmark brand-badge-word" aria-label="DEFT3R">DEFT3R</p>
            <p ref={headerAssistantRef} className="brand-badge-tag mt-1 font-semibold uppercase text-muted-foreground">Toplantı asistanı</p>
          </div>
        </button>
        {state === "meeting" && (
          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-2 text-sm font-semibold sm:flex">
              <i className={cn("listening-dot", isPaused && "listening-dot-paused")} /> {isPaused ? "Duraklatıldı" : "Dinliyor"}
            </span>
            <time className="font-mono text-sm font-semibold tabular-nums">
              {minutesLabel}:
              <span className="timer-seconds-window">
                <span key={secondsLabel} className="timer-seconds-tick">{secondsLabel}</span>
              </span>
            </time>
            <Button variant="quiet" size="sm" className="meeting-toolbar-btn rounded-full ring-1 ring-border" onClick={() => setIsPaused((value) => !value)}>
              {isPaused ? (<><Play className="size-4 fill-current" /> Devam Et</>) : (<><Pause className="size-4" /> Kaydı Durdur</>)}
            </Button>
            <Button variant="danger" size="sm" className="meeting-toolbar-btn meeting-toolbar-btn-danger rounded-full" onClick={endMeeting}><CircleStop className="size-4" /> Bitir</Button>
          </div>
        )}
        <button
          type="button"
          className="theme-toggle ml-3"
          onClick={toggleTheme}
          role="switch"
          aria-checked={isDark}
          aria-label={isDark ? "Gündüz moduna geç" : "Gece moduna geç"}
          title={isDark ? "Gündüz modu" : "Gece modu"}
        >
          <Sun className="theme-toggle-icon theme-toggle-sun" aria-hidden="true" />
          <Moon className="theme-toggle-icon theme-toggle-moon" aria-hidden="true" />
          <span className="theme-toggle-thumb" aria-hidden="true" />
        </button>
      </header>

      <section className="relative z-10 mx-auto flex min-h-[calc(100vh-84px)] max-w-5xl flex-col px-5 pb-8 sm:px-8">
        {state === "ready" && (
          <div className="ready-stage relative mx-auto flex w-full max-w-xl flex-1 flex-col items-center justify-center text-center">
            <div className={cn("mascot-enter relative", !introVisible && "intro-played")}>
              <span ref={readyShadowRef} className="mascot-shadow" />
              <span className="mascot-bob relative block w-[min(72vw,330px)]">
                <span className="mascot-look block">
                  <img ref={readyMascotRef} src={mascot} alt="Gülümseyen mavi defter maskotu" width={1024} height={1024} className="block w-full object-contain" />
                  <img src={blinkMascot} alt="" aria-hidden="true" width={1024} height={1024} className="mascot-blink absolute inset-0 w-full object-contain" />
                </span>
              </span>
              <span className="mascot-wave" aria-hidden="true">
                <span className="mascot-wave-tilt">
                  {introVisible ? <b>Merhaba<span className="typewriter-cursor" /></b> : <TypewriterGreeting />}
                  <i />
                </span>
              </span>
            </div>
            <h1 className="ready-heading mt-1 font-display text-3xl font-bold sm:text-4xl">Bugünkü toplantı ne hakkında?</h1>
            <div className="ready-paper-weight paper-weight" aria-hidden="true" />
            <div className="ready-meeting-card notebook-inset mt-6 w-full">
              <label htmlFor="meeting-title" className="sr-only">Toplantı adı</label>
              <textarea id="meeting-title" value={title} onChange={(event) => setTitle(event.target.value)} rows={2} placeholder="Toplantı adı" className="notebook-inset-field w-full resize-none px-4 py-3 text-sm outline-none placeholder:text-muted-foreground" />
              <Button className="notebook-inset-action mt-2 w-full" onClick={startMeeting}><Play className="size-4 fill-current" /> Toplantıyı Başlat</Button>
            </div>
            {notice && <p className="mt-3 text-sm font-medium text-destructive">{notice}</p>}
          </div>
        )}

        {state === "meeting" && (
          <div className="meeting-stage flex min-h-0 flex-1 flex-col">
            <div className="mx-auto mb-4 text-center">
              <h1 className="mt-1 font-display text-2xl font-bold sm:text-3xl">{title.trim() || "İsimsiz toplantı"}</h1>
            </div>
            <div className="transcript-scroll mx-auto flex w-full max-w-3xl flex-1 flex-col justify-end overflow-y-hidden px-1 pb-5">
              <div className="space-y-3">
                {conversation.slice(0, visibleMessages).map((message, index) => (
                  <article key={`${message.name}-${message.time}`} className={cn("speech-row bubble-in", index % 2 === 1 && "speech-row-alt")}>
                    <div className={cn("avatar", `avatar-${message.tone}`)}>{message.initials}</div>
                    <div className="speech-bubble">
                      <div className="mb-1 flex items-center justify-between gap-6"><strong className="text-xs">{message.name}</strong><time className="text-[10px] text-muted-foreground">{message.time}</time></div>
                      <p className="text-sm leading-relaxed">{message.text}</p>
                    </div>
                  </article>
                ))}
              </div>
            </div>
            <MeetingMascot startTop={travelStartTop} isPaused={isPaused} />
            {notice && <p className="mt-2 text-center text-xs font-medium text-destructive">{notice}</p>}
          </div>
        )}


        {state === "summary" && (
          <div className="summary-stage mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center">
            <div className="sleepy-mascot" role="img" aria-label="Yere oturmuş uyuyan DEFT3R maskotu">
              <span className="sleepy-z sleepy-z-1" aria-hidden="true">z</span>
              <span className="sleepy-z sleepy-z-2" aria-hidden="true">z</span>
              <span className="sleepy-z sleepy-z-3" aria-hidden="true">Z</span>
              <img src={sleepingMascot} alt="" width={1024} height={1024} className="sleepy-mascot-img object-contain" />
              <span className="sleepy-floor" aria-hidden="true" />
            </div>
            <p className="mt-2 text-xs font-semibold uppercase text-muted-foreground">Toplantı Tamamlandı</p>
            <h1 className="mt-2 text-center font-display text-3xl font-bold">{meetingName} · {time}</h1>


            <div className="mt-6 grid w-full gap-3 sm:grid-cols-2" aria-live="polite">
              <Button
                className="doc-action w-full"
                onClick={prepareSummary}
                disabled={summaryStatus === "working"}
              >
                {summaryStatus === "working" ? (<><Loader2 className="size-4 animate-spin" /> Toplantı özeti hazırlanıyor...</>)
                  : summaryStatus === "done" ? (<><Check className="size-4" /> Özet indirildi · tekrar al</>)
                  : (<><FileText className="size-4" /> Toplantı özeti al</>)}
              </Button>
              <Button
                variant="quiet"
                className="doc-action w-full"
                onClick={prepareTranscript}
                disabled={transcriptStatus === "working"}
              >
                {transcriptStatus === "working" ? (<><Loader2 className="size-4 animate-spin" /> Toplantı dökümü hazırlanıyor...</>)
                  : transcriptStatus === "done" ? (<><Check className="size-4" /> Toplantı dökümü indirildi · tekrar al</>)
                  : (<><Download className="size-4" /> Toplantı dökümünü indir</>)}
              </Button>
            </div>



            <Button variant="quiet" className="mt-5" onClick={reset}><RotateCcw className="size-4" /> Yeni toplantı</Button>
          </div>
        )}
      </section>
      {introVisible && (
        <div className={cn("opening-screen", introReady && "is-ready", introStarted && "is-started")}>
          <div className="opening-brand-lockup" style={introPlacement ?? undefined} aria-hidden="true">
            <img ref={openingLogoRef} src="/media/teb-ai-mark.png" alt="" className="opening-logo" />
            <p ref={openingNameRef} className="brand-wordmark opening-brand-name">DEFT3R</p>
            <p ref={openingAssistantRef} className="opening-brand-assistant">Toplantı asistanı</p>
            <p ref={openingPoweredRef} className="opening-brand-powered">made with pure hate ❤️ </p>
          </div>
          {introReady && !introStarted && (
            <button type="button" className="opening-start-btn" onClick={() => setIntroStarted(true)}>
              <Play className="size-4 fill-current" /> Başla
            </button>
          )}
          <div
            className="opening-jumping-mascot"
            style={introPlacement ?? undefined}
            aria-hidden="true"
            onAnimationEnd={(event) => {
              if (event.animationName === "opening-sit-jump") setIntroVisible(false);
            }}
          >
            <div className="opening-jump-arc">
              <div className="opening-sitting-pose">
                <img src={sittingMascotBody} alt="" className="opening-sitting-body" />
                <img src={sittingLegLeftLower} alt="" className="opening-sitting-shin opening-sitting-shin-left" />
                <img src={sittingLegRightLower} alt="" className="opening-sitting-shin opening-sitting-shin-right" />
                <img src={sittingLegLeftUpper} alt="" className="opening-sitting-thigh" />
                <img src={sittingLegRightUpper} alt="" className="opening-sitting-thigh" />
              </div>
              <img src={mascot} alt="" className="opening-landing-pose" />
            </div>
          </div>
          <span className="opening-landing-shadow" style={introPlacement ?? undefined} aria-hidden="true" />
        </div>
      )}
    </main>
  );
}

function MeetingMascot({ startTop, isPaused }: { startTop: number | null; isPaused: boolean }) {
  return (
    <div className="meeting-mascot" role="img" aria-label="Açılıp toplantı notlarını yazan DEFT3R maskotu">
      <ChromaKeyVideo
        src={writingVideo}
        poster={mascot}
        className="meeting-mascot-video"
        {...(startTop !== null
          ? { style: { "--travel-start-top": `${startTop}px` } as CSSProperties }
          : {})}
        paused={isPaused}
      />
    </div>
  );
}

const GREETINGS = ["Merhaba", "Hello", "Hola", "Bonjour", "Ciao", "Hallo", "Olá", "こんにちは", "你好", "Привет"];

type TypewriterPhase = "typing" | "holding" | "deleting";

function TypewriterGreeting() {
  const [text, setText] = useState("");
  const [wordIndex, setWordIndex] = useState(0);
  const [phase, setPhase] = useState<TypewriterPhase>("typing");

  useEffect(() => {
    const word = GREETINGS[wordIndex % GREETINGS.length]!;
    let timer: number;

    if (phase === "typing") {
      if (text.length < word.length) {
        timer = window.setTimeout(() => setText(word.slice(0, text.length + 1)), 95);
      } else {
        timer = window.setTimeout(() => setPhase("holding"), 1200);
      }
    } else if (phase === "holding") {
      timer = window.setTimeout(() => setPhase("deleting"), 700);
    } else {
      if (text.length > 0) {
        timer = window.setTimeout(() => setText(word.slice(0, text.length - 1)), 40);
      } else {
        timer = window.setTimeout(() => {
          setWordIndex((index) => (index + 1) % GREETINGS.length);
          setPhase("typing");
        }, 250);
      }
    }

    return () => window.clearTimeout(timer);
  }, [text, phase, wordIndex]);

  return (
    <b>
      {text}
      <span className="typewriter-cursor" aria-hidden="true" />
    </b>
  );
}
