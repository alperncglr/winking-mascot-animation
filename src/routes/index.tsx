import { createFileRoute } from "@tanstack/react-router";
import { Check, ChevronDown, CircleStop, Download, FileText, Loader2, Moon, Pause, Play, RotateCcw, Sparkles, Sun } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type Ref } from "react";

import { ChromaKeyVideo } from "@/components/chroma-key-video";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { LiveAudioStream, type LiveSegmentEvent, type LiveAudioStatus } from "@/lib/liveAudioStream";
import { downloadMeetingPdf, type MeetingPdfMetadata } from "@/lib/meetingPdf";
import {
  ApiError,
  createMeeting,
  finishMeeting,
  getMeeting,
  getTranscript,
  getWebSocketUrl,
  postSummary,
  requestWebSocketToken,
  startRecording,
  stopRecording,
  type MeetingDetail,
  type MeetingLanguage,
  type MeetingStatus,
  type SummaryContent,
} from "@/lib/meetingScribeApi";
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
      { title: "T3AI DEFT3R — Akıllı Toplantı Defteri" },
      { name: "description", content: "Toplantıları gerçek zamanlı yazıya döken ve özetleyen sevimli dijital defter." },
      { property: "og:title", content: "T3AI DEFT3R — Akıllı Toplantı Defteri" },
      { property: "og:description", content: "Toplantıları gerçek zamanlı yazıya döken ve özetleyen sevimli dijital defter." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

type AppState = "ready" | "meeting" | "closing" | "summary";
type DocStatus = "idle" | "working" | "done" | "error";
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
  "--intro-name-t3-dx": string;
  "--intro-name-t3-dy": string;
  "--intro-name-defter-dx": string;
  "--intro-name-defter-dy": string;
  "--intro-assistant-dx": string;
  "--intro-assistant-dy": string;
  "--intro-powered-dx": string;
  "--intro-powered-dy": string;
  "--intro-shadow-left": string;
  "--intro-shadow-top": string;
  "--intro-shadow-width": string;
  "--intro-shadow-height": string;
};

const AVATAR_TONES = ["coral", "blue", "yellow"] as const;

const meetingLanguages: ReadonlyArray<{
  value: MeetingLanguage;
  short: string;
  name: string;
}> = [
  { value: "mixed", short: "Otomatik", name: "Otomatik / Çok dilli" },
  { value: "tr", short: "TR Türkçe", name: "Türkçe" },
  { value: "en", short: "GB English", name: "İngilizce" },
  { value: "de", short: "DE Deutsch", name: "Almanca" },
  { value: "fr", short: "FR Français", name: "Fransızca" },
];

// Backend konuşmacıları "Kullanıcı N" olarak saklıyor. Görünümdeki ad farklı
// olabilir; numara üzerinden aynı renk ve kısa etiketi koruyoruz.
function displaySpeakerLabel(label: string): string {
  return label.replace(/^Kullanıcı (?=\d+\b)/, "Katılımcı ");
}

function speakerVisual(label: string) {
  const match = label.match(/\d+/);
  const number = match ? Number(match[0]) : 1;
  const tone = AVATAR_TONES[(number - 1) % AVATAR_TONES.length]!;
  return { tone, initials: `K${number}`, number };
}

function BrandWord({ word, wordRef, className }: {
  word: string;
  wordRef: Ref<HTMLSpanElement>;
  className?: string;
}) {
  return (
    <span ref={wordRef} className={cn("brand-token-shell", className)}>
      <span className="brand-name-token">{word}</span>
    </span>
  );
}

function Index() {
  const [state, setState] = useState<AppState>("ready");
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState<MeetingLanguage>("mixed");
  const [languageOpen, setLanguageOpen] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [notice, setNotice] = useState("");
  const [meetingId, setMeetingId] = useState<number | null>(null);
  const [meetingDetail, setMeetingDetail] = useState<MeetingDetail | null>(null);
  const [liveSegments, setLiveSegments] = useState<LiveSegmentEvent[]>([]);
  const [transcriptContent, setTranscriptContent] = useState<string | null>(null);
  const [summaryContent, setSummaryContent] = useState<SummaryContent | null>(null);
  const [summaryStatus, setSummaryStatus] = useState<DocStatus>("idle");
  const [transcriptStatus, setTranscriptStatus] = useState<DocStatus>("idle");
  const [summaryDownloaded, setSummaryDownloaded] = useState(false);
  const [transcriptDownloaded, setTranscriptDownloaded] = useState(false);
  const [travelStartTop, setTravelStartTop] = useState<number | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [isDark, setIsDark] = useState(false);
  const [introVisible, setIntroVisible] = useState(true);
  const [introReady, setIntroReady] = useState(false);
  const [introStarted, setIntroStarted] = useState(false);
  const [introPlacement, setIntroPlacement] = useState<IntroPlacement | null>(null);
  const audioStreamRef = useRef<LiveAudioStream | null>(null);
  const audioAttemptRef = useRef(0);
  const pausedRef = useRef(false);
  const startingMeetingRef = useRef(false);
  const pendingMeetingIdRef = useRef<number | null>(null);
  const [isStartingMeeting, setIsStartingMeeting] = useState(false);
  const [audioStatus, setAudioStatus] = useState<LiveAudioStatus>({ connection: "closed", microphone: "stopped", delivery: "waiting" });
  const pendingTranscriptDownloadRef = useRef(false);
  const pendingSummaryDownloadRef = useRef(false);
  const transcriptScrollRef = useRef<HTMLDivElement>(null);
  const readyMascotRef = useRef<HTMLImageElement>(null);
  const readyShadowRef = useRef<HTMLSpanElement>(null);
  const headerLogoRef = useRef<HTMLImageElement>(null);
  const headerNameT3Ref = useRef<HTMLSpanElement>(null);
  const headerNameDefterRef = useRef<HTMLSpanElement>(null);
  const headerAssistantTextRef = useRef<HTMLSpanElement>(null);
  const openingLogoRef = useRef<HTMLImageElement>(null);
  const openingNameT3Ref = useRef<HTMLSpanElement>(null);
  const openingNameDefterRef = useRef<HTMLSpanElement>(null);
  const openingAssistantTextRef = useRef<HTMLSpanElement>(null);
  const openingPoweredRef = useRef<HTMLParagraphElement>(null);
  const selectedLanguage = meetingLanguages.find((option) => option.value === language) ?? meetingLanguages[0]!;

  function measureIntroBrandLanding() {
    const sourceLogo = openingLogoRef.current?.getBoundingClientRect();
    const sourceNameT3 = openingNameT3Ref.current?.getBoundingClientRect();
    const sourceNameDefter = openingNameDefterRef.current?.getBoundingClientRect();
    const sourceAssistant = openingAssistantTextRef.current?.getBoundingClientRect();
    const sourcePowered = openingPoweredRef.current?.getBoundingClientRect();
    const targetLogo = headerLogoRef.current?.getBoundingClientRect();
    const targetNameT3 = headerNameT3Ref.current?.getBoundingClientRect();
    const targetNameDefter = headerNameDefterRef.current?.getBoundingClientRect();
    const targetAssistant = headerAssistantTextRef.current?.getBoundingClientRect();

    if (!sourceLogo || !sourceNameT3 || !sourceNameDefter || !sourceAssistant || !sourcePowered || !targetLogo || !targetNameT3 || !targetNameDefter || !targetAssistant) {
      return null;
    }

    const positionDelta = (source: DOMRect, target: DOMRect) => ({
      x: target.left - source.left,
      y: target.top - source.top,
    });
    const logo = {
      ...positionDelta(sourceLogo, targetLogo),
      scale: targetLogo.width / sourceLogo.width,
    };
    const nameT3 = positionDelta(sourceNameT3, targetNameT3);
    const nameDefter = positionDelta(sourceNameDefter, targetNameDefter);
    const assistant = positionDelta(sourceAssistant, targetAssistant);
    const powered = positionDelta(sourcePowered, targetAssistant);

    return {
      "--intro-logo-dx": `${logo.x}px`,
      "--intro-logo-dy": `${logo.y}px`,
      "--intro-logo-scale": `${logo.scale}`,
      "--intro-name-t3-dx": `${nameT3.x}px`,
      "--intro-name-t3-dy": `${nameT3.y}px`,
      "--intro-name-defter-dx": `${nameDefter.x}px`,
      "--intro-name-defter-dy": `${nameDefter.y}px`,
      "--intro-assistant-dx": `${assistant.x}px`,
      "--intro-assistant-dy": `${assistant.y}px`,
      "--intro-powered-dx": `${powered.x}px`,
      "--intro-powered-dy": `${powered.y}px`,
    } satisfies Partial<IntroPlacement>;
  }

  function startIntro() {
    const brandLanding = measureIntroBrandLanding();
    if (!brandLanding) {
      setIntroStarted(true);
      return;
    }

    setIntroPlacement((current) => current && ({
      ...current,
      ...brandLanding,
    }));

    // Yeni hedef koordinatları DOM'a uygulandıktan sonra animasyonu başlat.
    window.requestAnimationFrame(() => setIntroStarted(true));
  }

  useLayoutEffect(() => {
    const placeIntro = () => {
      const target = readyMascotRef.current?.getBoundingClientRect();
      const targetShadow = readyShadowRef.current?.getBoundingClientRect();
      if (!target || !targetShadow || target.width === 0) return;

      const startWidth = Math.min(window.innerWidth * 0.34, 170);
      // Oturan maskotu logonun üzerinde optik olarak ortala: görselin sol
      // elindeki saydam boşluk nedeniyle geometrik merkez biraz solda kalıyor.
      const startHorizontalOffset = Math.min(24, startWidth * 0.135);
      const startLeft = (window.innerWidth - startWidth) / 2 + startHorizontalOffset;
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
        "--intro-name-t3-dx": "0px",
        "--intro-name-t3-dy": "0px",
        "--intro-name-defter-dx": "0px",
        "--intro-name-defter-dy": "0px",
        "--intro-assistant-dx": "0px",
        "--intro-assistant-dy": "0px",
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
      const brandLanding = measureIntroBrandLanding();
      if (!brandLanding) return;

      // Her metin parçası kendi başlangıç ve hedef piksel koordinatlarıyla
      // eşleşir; animasyon sırasında hizalama modu değiştirilmez.
      setIntroPlacement((current) => current && ({
        ...current,
        ...brandLanding,
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
    const preloadSleepingMascot = new Image();
    preloadSleepingMascot.src = sleepingMascot;
  }, []);

  useEffect(() => {
    if (state !== "meeting" || isPaused) return;
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [state, isPaused]);

  // Yeni/güncellenen segment geldiğinde sohbet alanını otomatik en alta
  // kaydır — gerçek scroll açık olduğu için bunu elle yapmazsak kullanıcı
  // her mesajda manuel aşağı kaydırmak zorunda kalır.
  useEffect(() => {
    transcriptScrollRef.current?.scrollTo({ top: transcriptScrollRef.current.scrollHeight, behavior: "smooth" });
  }, [liveSegments]);

  useEffect(() => () => {
    audioAttemptRef.current += 1;
    audioStreamRef.current?.stop();
  }, []);

  // Sonuç ekranı, uzun süren bir işlem sırasında sayfa yenilense bile toplantı
  // kimliğiyle tekrar açılabilsin. Örnek: /?meeting=88
  useEffect(() => {
    const rawMeetingId = new URLSearchParams(window.location.search).get('meeting');
    if (!rawMeetingId) return;
    const recoveredMeetingId = Number(rawMeetingId);
    if (!Number.isSafeInteger(recoveredMeetingId) || recoveredMeetingId <= 0) return;
    setMeetingId(recoveredMeetingId);
    setState('summary');
    setIntroVisible(false);
    setSummaryStatus('idle');
    setTranscriptStatus('idle');
  }, []);

  // Toplantı bitince transkript ve özet arka planda otomatik hazırlanmaya
  // başlıyor (backend: kayıt durunca zincirleniyor). Burada durumu birkaç
  // saniyede bir kontrol edip hazır olan içerikleri anında yakalıyoruz —
  // kullanıcı butona basmadan önce her şey zaten hazırlanmış olabiliyor.
  useEffect(() => {
    if (state !== "summary" || meetingId === null) return;
    let cancelled = false;
    let timeoutId = 0;
    let haveTranscript = false;
    let haveSummary = false;
    let attempts = 0;

    const transcriptReadyStatuses: MeetingStatus[] = [
      "refined",
      "summarizing",
      "summary_pending",
      "summarized",
      "summary_failed",
    ];

    async function poll() {
      attempts += 1;
      try {
        const meeting = await getMeeting(meetingId!);
        if (cancelled) return;
        setMeetingDetail(meeting);

        if (!haveTranscript && transcriptReadyStatuses.includes(meeting.status)) {
          try {
            const { transcript } = await getTranscript(meetingId!);
            if (!cancelled) {
              setTranscriptContent(transcript);
              setTranscriptStatus("done");
              haveTranscript = true;
              // Kullanıcı hazır olmadan "indir"e bastıysa, tekrar tıklamasını
              // beklemeden hazır olur olmaz otomatik indir.
              if (pendingTranscriptDownloadRef.current) {
                pendingTranscriptDownloadRef.current = false;
                await downloadTranscriptPdf(transcript, meeting);
                setTranscriptDownloaded(true);
              }
            }
          } catch {
            // Backend henüz dosyayı yazmamış olabilir, bir sonraki turda tekrar denenir.
          }
        }
        if (meeting.status === "refine_failed" && !cancelled) {
          setTranscriptStatus("error");
          haveTranscript = true;
          pendingTranscriptDownloadRef.current = false;
        }

        if (meeting.status === "summarized" && meeting.summary && !cancelled) {
          setSummaryContent(meeting.summary);
          setSummaryStatus("done");
          haveSummary = true;
          if (pendingSummaryDownloadRef.current) {
            pendingSummaryDownloadRef.current = false;
            await downloadSummaryPdf(meeting.summary, meeting);
            setSummaryDownloaded(true);
          }
        } else if (meeting.status === "summary_failed" && !cancelled) {
          setSummaryStatus("error");
          haveSummary = true;
          pendingSummaryDownloadRef.current = false;
        }

        if (!cancelled && !(haveTranscript && haveSummary)) {
          // Uzun toplantıların offline Whisper/Pyannote işlemi beş dakikayı
          // aşabilir. İlk beş dakika hızlı, sonrasında daha seyrek kontrol et;
          // içerikler hazır olana kadar polling'i tamamen bırakma.
          const delayMs = attempts < 100 ? 3000 : 10000;
          timeoutId = window.setTimeout(poll, delayMs);
        }
      } catch {
        if (!cancelled) {
          const delayMs = attempts < 100 ? 3000 : 10000;
          timeoutId = window.setTimeout(poll, delayMs);
        }
      }
    }

    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [state, meetingId]);

  async function beginLiveAudio(id: number) {
    const attempt = ++audioAttemptRef.current;
    setAudioStatus({ connection: "connecting", microphone: "starting", delivery: "waiting" });
    let stream: LiveAudioStream | null = null;
    try {
      const { token } = await requestWebSocketToken();
      if (attempt !== audioAttemptRef.current) return;
      stream = new LiveAudioStream();
      audioStreamRef.current = stream;
      await stream.start(getWebSocketUrl(id, token), {
        onStatus: (status) => { if (audioStreamRef.current === stream && attempt === audioAttemptRef.current) setAudioStatus(status); },
        onSegment: (event) => {
          if (attempt !== audioAttemptRef.current) return;
          setLiveSegments((prev) => {
            const index = prev.findIndex((item) => item.segment_id === event.segment_id);
            if (index === -1) return [...prev, event];
            if ((prev[index]!.revision ?? 0) > (event.revision ?? 0)) return prev;
            const next = [...prev];
            next[index] = event;
            return next;
          });
        },
        onError: (message) => { if (attempt === audioAttemptRef.current) setNotice(message); },
        onClose: () => {
          if (audioStreamRef.current === stream) audioStreamRef.current = null;
        },
      });
      if (attempt !== audioAttemptRef.current) stream.stop();
    } catch (error) {
      stream?.stop();
      if (attempt !== audioAttemptRef.current) return;
      if (audioStreamRef.current === stream || stream === null) {
        audioStreamRef.current = null;
        setAudioStatus({ connection: "closed", microphone: "stopped", delivery: "waiting" });
        setNotice(error instanceof Error ? error.message : "Mikrofon başlatılamadı.");
      }
    }
  }

  async function startMeeting() {
    if (startingMeetingRef.current) return;
    startingMeetingRef.current = true;
    setIsStartingMeeting(true);
    setNotice("");
    const rect = readyMascotRef.current?.getBoundingClientRect();
    setTravelStartTop(rect ? rect.top : null);
    try {
      let meeting_id = pendingMeetingIdRef.current;
      if (meeting_id === null) {
        const created = await createMeeting(title.trim(), language);
        meeting_id = created.meeting_id;
        pendingMeetingIdRef.current = meeting_id;
        setMeetingId(meeting_id);
      }
      try {
        await startRecording(meeting_id);
      } catch (error) {
        // A lost HTTP response can mean recording actually started. Do not
        // create another meeting on retry; confirm the existing one's state.
        const detail = await getMeeting(meeting_id);
        if (detail.status !== "recording") throw error;
      }
      pendingMeetingIdRef.current = null;
      setMeetingId(meeting_id);
      setLiveSegments([]);
      setTranscriptContent(null);
      setSummaryContent(null);
      setSummaryDownloaded(false);
      setTranscriptDownloaded(false);
      setSeconds(0);
      pausedRef.current = false;
      setIsPaused(false);
      setState("meeting");
      await beginLiveAudio(meeting_id);
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : "Toplantı başlatılamadı, bağlantıyı kontrol edin.");
    } finally {
      startingMeetingRef.current = false;
      setIsStartingMeeting(false);
    }
  }

  async function togglePause() {
    const next = !pausedRef.current;
    pausedRef.current = next;
    setIsPaused(next);
    if (next) {
      // Keep the WebSocket and Diart session alive so speaker identities do
      // not restart every time recording is paused within this meeting.
      audioStreamRef.current?.pause();
    } else if (meetingId !== null) {
      setNotice("");
      const stream = audioStreamRef.current;
      if (stream?.isConnected()) {
        try {
          await stream.resume();
        } catch (error) {
          pausedRef.current = true;
          setIsPaused(true);
          stream.pause();
          setNotice(error instanceof Error ? error.message : "Mikrofon yeniden başlatılamadı.");
        }
      } else {
        // A real network disconnect cannot preserve Diart's in-memory state.
        stream?.stop();
        if (audioStreamRef.current === stream) audioStreamRef.current = null;
        setNotice("Canlı bağlantı koptu; yeniden bağlanırken konuşmacılar tekrar tanınabilir.");
        await beginLiveAudio(meetingId);
      }
    }
  }

  async function endMeeting() {
    audioAttemptRef.current += 1;
    audioStreamRef.current?.stop();
    audioStreamRef.current = null;
    pausedRef.current = false;
    setIsPaused(false);
    setState("summary");
    setSummaryStatus("idle");
    setTranscriptStatus("idle");
    setSummaryDownloaded(false);
    setTranscriptDownloaded(false);
    if (meetingId !== null) {
      try {
        // WebSocket kapandıktan sonra backend son kısa ses parçasını manifest'e
        // yazıyor. Özellikle kısa/ardışık toplantılarda ilk stop isteği bu
        // işlemden önce ulaşarak 409 dönebilir. Offline zinciri kuyruklanana
        // kadar yalnızca bu geçici 409 durumunu kontrollü biçimde yeniden dene.
        let queued = false;
        let lastError: unknown;
        for (let attempt = 0; attempt < 30 && !queued; attempt += 1) {
          try {
            await stopRecording(meetingId);
            queued = true;
          } catch (error) {
            lastError = error;
            if (!(error instanceof ApiError) || error.status !== 409 || attempt === 29) {
              throw error;
            }
            await new Promise((resolve) => window.setTimeout(resolve, 1000));
          }
        }
        if (!queued) throw lastError;
      } catch (error) {
        setNotice(error instanceof ApiError ? error.message : "Kayıt durdurulurken bir sorun oluştu.");
      }
    }
  }

  function reset() {
    audioAttemptRef.current += 1;
    audioStreamRef.current?.stop();
    audioStreamRef.current = null;
    pausedRef.current = false;
    pendingMeetingIdRef.current = null;
    if (meetingId !== null) {
      // En son adım: geçici ses dosyalarını temizle. Henüz hazır değilse
      // (özet/transkript bitmediyse) backend 409 döner — göz ardı edilir.
      void finishMeeting(meetingId).catch(() => {});
    }
    setState("ready");
    setLanguage("mixed");
    setMeetingId(null);
    setMeetingDetail(null);
    setLiveSegments([]);
    setTranscriptContent(null);
    setSummaryContent(null);
    setSeconds(0);
    setNotice("");
    setSummaryStatus("idle");
    setTranscriptStatus("idle");
    setSummaryDownloaded(false);
    setTranscriptDownloaded(false);
    setIsPaused(false);
  }

  const minutesLabel = String(Math.floor(seconds / 60)).padStart(2, "0");
  const secondsLabel = String(seconds % 60).padStart(2, "0");
  const time = `${minutesLabel}:${secondsLabel}`;
  const meetingName = title.trim() || "İsimsiz toplantı";

  function slug() {
    return meetingName.toLocaleLowerCase("tr").replace(/[^a-z0-9ğüşiöç]+/gi, "-").replace(/^-|-$/g, "") || "toplanti";
  }

  function dateStamp(detail = meetingDetail) {
    const meetingDate = detail?.date ? new Date(detail.date) : new Date();
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Istanbul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(meetingDate);
    const part = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((item) => item.type === type)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}`;
  }

  function pdfMetadata(detail = meetingDetail): MeetingPdfMetadata {
    const timeZone = "Europe/Istanbul";
    const startedAt = detail?.date ? new Date(detail.date) : new Date();
    const durationSec = detail?.duration_sec ?? seconds;
    const endedAt = new Date(startedAt.getTime() + durationSec * 1000);
    const dateFormatter = new Intl.DateTimeFormat("tr-TR", {
      timeZone,
      day: "2-digit",
      month: "long",
      year: "numeric",
    });
    const timeFormatter = new Intl.DateTimeFormat("tr-TR", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    const durationHours = String(Math.floor(durationSec / 3600)).padStart(2, "0");
    const durationMinutes = String(Math.floor((durationSec % 3600) / 60)).padStart(2, "0");
    const durationSeconds = String(Math.floor(durationSec % 60)).padStart(2, "0");
    return {
      meetingName: detail?.title?.trim() || meetingName,
      date: dateFormatter.format(startedAt),
      startTime: timeFormatter.format(startedAt),
      endTime: timeFormatter.format(endedAt),
      duration: `${durationHours}:${durationMinutes}:${durationSeconds}`,
      timeZone: "İstanbul (UTC+3)",
    };
  }

  async function downloadTranscriptPdf(transcript: string, detail = meetingDetail) {
    await downloadMeetingPdf({
      fileName: `${slug()}-${dateStamp(detail)}-transkript.pdf`,
      documentTitle: "Toplantı Dökümü",
      sectionTitle: "Konuşma Dökümü",
      metadata: pdfMetadata(detail),
      body: transcript,
      kind: "transcript",
    });
  }

  async function downloadSummaryPdf(summary: SummaryContent, detail = meetingDetail) {
    await downloadMeetingPdf({
      fileName: `${slug()}-${dateStamp(detail)}-ozet.pdf`,
      documentTitle: "Toplantı Özeti",
      sectionTitle: "",
      metadata: pdfMetadata(detail),
      body: summary.summary_md,
      kind: "summary",
    });
  }

  async function prepareSummary() {
    if (summaryContent) {
      await downloadSummaryPdf(summaryContent);
      setSummaryDownloaded(true);
      return;
    }
    if (!meetingId || summaryStatus === "working") return;
    setNotice("");
    setSummaryStatus("working");
    try {
      const meeting = await getMeeting(meetingId);
      let result: SummaryContent | null = meeting.status === "summarized" ? meeting.summary : null;

      // Arka plandaki otomatik özet daha önce başarısız olduysa bu tıklama
      // gerçek bir yeniden deneme işlevi görür. Devam eden işlemle yarışmamak
      // için diğer durumlarda yeni özet isteği başlatılmaz.
      if (!result && meeting.status === "summary_failed") {
        result = await postSummary(meetingId);
      }

      if (!result) {
        // Henüz hazır değil; arka plan işlemi bitince poll efekti otomatik
        // indirmeyi tetikleyecek, kullanıcının tekrar tıklaması gerekmeyecek.
        pendingSummaryDownloadRef.current = true;
        setNotice("Toplantı özeti arka planda hazırlanıyor, hazır olunca otomatik inecek.");
        return;
      }
      setSummaryContent(result);
      setSummaryStatus("done");
      await downloadSummaryPdf(result, meeting);
      setSummaryDownloaded(true);
    } catch (error) {
      pendingSummaryDownloadRef.current = false;
      setSummaryStatus("error");
      setNotice(error instanceof ApiError ? error.message : "Özet alınamadı.");
    }
  }

  async function prepareTranscript() {
    if (transcriptContent) {
      await downloadTranscriptPdf(transcriptContent);
      setTranscriptDownloaded(true);
      return;
    }
    if (!meetingId || transcriptStatus === "working") return;
    setNotice("");
    setTranscriptStatus("working");
    try {
      const { transcript } = await getTranscript(meetingId);
      setTranscriptContent(transcript);
      setTranscriptStatus("done");
      await downloadTranscriptPdf(transcript);
      setTranscriptDownloaded(true);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Henüz hazır değil; arka plan işlemi bitince poll efekti otomatik
        // indirmeyi tetikleyecek, kullanıcının tekrar tıklaması gerekmeyecek.
        pendingTranscriptDownloadRef.current = true;
        setNotice("Toplantı dökümü arka planda hazırlanıyor, hazır olunca otomatik inecek.");
        return;
      }
      pendingTranscriptDownloadRef.current = false;
      setTranscriptStatus("error");
      setNotice(error instanceof ApiError ? error.message : "Transkript alınamadı.");
    }
  }

  return (
    <main className={cn("app-shell relative flex h-screen flex-col overflow-hidden bg-background text-foreground", state === "ready" && "ready-shell", introVisible && "intro-active", introVisible && introStarted && "intro-ready")}>
      <div aria-hidden="true" className="paper-surface absolute inset-0" />
      <div aria-hidden="true" className="paper-grid absolute inset-0" />
      <header className={cn("app-header relative z-20 mx-auto flex w-full max-w-6xl items-center justify-end px-5 py-5 sm:px-8", state !== "meeting" && "brand-hero")}>
        <button className="brand-badge" onClick={reset} aria-label="T3AI DEFT3R başlangıç ekranı">
          <span className="brand-badge-mascot">
            {state === "meeting" ? (
              <img src="/media/deft3r-meeting-header-mascot.png" alt="" className="block h-full w-full object-contain" />
            ) : (
              <span className="mascot-look block h-full w-full">
                <img src={mascot} alt="" width={1024} height={1024} className="block h-full w-full object-contain" />
                <img src={blinkMascot} alt="" aria-hidden="true" width={1024} height={1024} className="mascot-blink absolute inset-0 h-full w-full object-contain" />
              </span>
            )}
          </span>
          <img ref={headerLogoRef} src="/media/teb-ai-mark.png" alt="TEB AI logosu" className="brand-badge-teb object-contain" />
          <div className="brand-badge-text text-left leading-none">
            <p className="brand-wordmark brand-badge-word" aria-label="T3AI DEFT3R">
              <BrandWord wordRef={headerNameT3Ref} word="T3AI" />{" "}
              <BrandWord wordRef={headerNameDefterRef} word="DEFT3R" />
            </p>
            <p className="brand-badge-tag mt-1 font-semibold uppercase text-muted-foreground">
              <span ref={headerAssistantTextRef}>Toplantı asistanı</span>
            </p>
          </div>
        </button>
        {state === "meeting" && (
          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-2 text-sm font-semibold sm:flex">
              <i className={cn("listening-dot", (isPaused || audioStatus.microphone !== "listening" || audioStatus.connection !== "connected") && "listening-dot-paused")} /> {isPaused ? "Duraklatıldı" : audioStatus.connection !== "connected" ? "Bağlantı yok" : audioStatus.microphone === "listening" ? "Mikrofon dinliyor" : "Ses akışı bekleniyor"}
            </span>
            <time className="font-mono text-sm font-semibold tabular-nums">
              {minutesLabel}:
              <span className="timer-seconds-window">
                <span key={secondsLabel} className="timer-seconds-tick">{secondsLabel}</span>
              </span>
            </time>
            <Button variant="quiet" size="sm" className="meeting-toolbar-btn rounded-full ring-1 ring-border" onClick={() => void togglePause()}>
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

      {state === "meeting" && <div role="status" className="relative z-10 mx-auto mb-3 flex w-full max-w-5xl flex-wrap gap-x-5 gap-y-1 px-5 text-xs text-muted-foreground sm:px-8">
        <span>Toplantı #{meetingId}</span>
        <span>{audioStatus.connection === "connected" ? "● Bağlantı kurulu" : audioStatus.connection === "connecting" ? "○ Bağlanıyor" : "○ Bağlantı kapalı"}</span>
        <span>{isPaused ? "Mikrofon duraklatıldı" : audioStatus.microphone === "listening" ? "Mikrofon dinliyor" : audioStatus.microphone === "muted" ? "Mikrofon sessize alındı" : audioStatus.microphone === "stalled" ? "Mikrofon akışı durdu" : audioStatus.microphone === "starting" ? "Mikrofon açılıyor" : "Mikrofon kapalı"}</span>
        <span>{audioStatus.connection !== "connected" ? "Ses gönderilmiyor" : audioStatus.delivery === "confirmed" ? "Sunucu sesi alıyor" : audioStatus.delivery === "delayed" ? "Sunucudan ses alındı onayı gecikiyor" : "Ses alındı onayı bekleniyor"}</span>
      </div>}

      <section className="relative z-10 mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col px-5 pb-8 sm:px-8">
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
              <textarea id="meeting-title" value={title} onChange={(event) => setTitle(event.target.value)} rows={2} placeholder="Toplantı adı" className="notebook-inset-field w-full resize-none px-4 pb-1 pt-5 text-sm outline-none placeholder:text-muted-foreground" />
              <div className={cn("language-picker mt-2", languageOpen && "is-open")}>
                <Button
                  type="button"
                  variant="ghost"
                  className="language-picker-trigger"
                  aria-expanded={languageOpen}
                  aria-controls="meeting-language-options"
                  onClick={() => setLanguageOpen((open) => !open)}
                >
                  <span className="language-picker-copy">
                    <span className="language-picker-label">Toplantı dili</span>
                    <span className="language-picker-value">{selectedLanguage.name}</span>
                  </span>
                  <ChevronDown className="language-picker-chevron" aria-hidden="true" />
                </Button>
                <div id="meeting-language-options" className="language-options" role="listbox" aria-label="Toplantı dili">
                  {meetingLanguages.map((option) => (
                    <Button
                      key={option.value}
                      type="button"
                      variant="ghost"
                      size="sm"
                      role="option"
                      aria-selected={language === option.value}
                      className={cn("language-option", language === option.value && "is-selected")}
                      onClick={() => {
                        setLanguage(option.value);
                        setLanguageOpen(false);
                      }}
                    >
                      {option.value === "mixed" && <Sparkles className="size-3.5" aria-hidden="true" />}
                      <span>{option.short}</span>
                    </Button>
                  ))}
                </div>
              </div>
              <Button className="notebook-inset-action mt-2 w-full" onClick={startMeeting} disabled={isStartingMeeting}><Play className="size-4 fill-current" /> {isStartingMeeting ? "Toplantı başlatılıyor…" : "Toplantıyı Başlat"}</Button>
            </div>
            {notice && <p className="mt-3 text-sm font-medium text-destructive">{notice}</p>}
          </div>
        )}

        {state === "meeting" && (
          <div className="meeting-stage flex min-h-0 flex-1 flex-col">
            <div className="mx-auto mb-4 text-center">
              <h1 className="mt-1 font-display text-2xl font-bold sm:text-3xl">{title.trim() || "İsimsiz toplantı"}</h1>
            </div>
            <div ref={transcriptScrollRef} className="transcript-scroll mx-auto flex w-full max-w-3xl flex-1 flex-col overflow-y-auto px-1 pb-5">
              <div className="mt-auto space-y-3">
                {liveSegments.map((segment) => {
                  const { tone, initials, number } = speakerVisual(segment.speaker_label);
                  return (
                    <article
                      key={segment.segment_id}
                      className={cn(
                        "speech-row bubble-in",
                        number % 2 === 0 && "speech-row-alt",
                        segment.status === "provisional" && "opacity-70",
                      )}
                    >
                      <div className={cn("avatar", `avatar-${tone}`)}>{initials}</div>
                      <div className="speech-bubble">
                        <div className="mb-1 flex items-center justify-between gap-6">
                          <strong className="text-xs">{displaySpeakerLabel(segment.speaker_label)}</strong>
                          {segment.status === "provisional" && <span className="text-[10px] text-muted-foreground">{!isPaused && audioStatus.connection === "connected" && audioStatus.delivery === "confirmed" && audioStatus.microphone === "listening" ? "yazıyor…" : "kesinleşmemiş metin"}</span>}
                        </div>
                        <p className="text-sm leading-relaxed">{segment.text}</p>
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>
            <div className="transcript-bottom-blur" aria-hidden="true" />
            <MeetingMascot startTop={travelStartTop} isPaused={isPaused} />
            {notice && <p className="mt-2 text-center text-xs font-medium text-destructive">{notice}</p>}
          </div>
        )}


        {state === "summary" && (
          <div className="summary-stage mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center">
            <div className="sleepy-mascot" role="img" aria-label="Yere oturmuş uyuyan T3AI DEFT3R maskotu">
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
                onClick={() => void prepareSummary()}
                disabled={summaryStatus === "working"}
              >
                {summaryStatus === "working" ? (<><Loader2 className="size-4 animate-spin" /> Toplantı özeti hazırlanıyor...</>)
                  : summaryStatus === "error" ? (<><FileText className="size-4" /> Tekrar dene</>)
                  : summaryDownloaded ? (<><Check className="size-4" /> Özet indirildi · tekrar indir</>)
                  : (<><FileText className="size-4" /> Toplantı özetini PDF indir</>)}
              </Button>
              <Button
                variant="quiet"
                className="doc-action w-full"
                onClick={() => void prepareTranscript()}
                disabled={transcriptStatus === "working"}
              >
                {transcriptStatus === "working" ? (<><Loader2 className="size-4 animate-spin" /> Toplantı dökümü hazırlanıyor...</>)
                  : transcriptStatus === "error" ? (<><Download className="size-4" /> Tekrar dene</>)
                  : transcriptDownloaded ? (<><Check className="size-4" /> Döküm indirildi · tekrar indir</>)
                  : (<><Download className="size-4" /> Toplantı dökümünü PDF indir</>)}
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
            <p className="brand-wordmark opening-brand-name" aria-label="T3AI DEFT3R">
              <BrandWord wordRef={openingNameT3Ref} word="T3AI" className="opening-text-token opening-name-t3" />{" "}
              <BrandWord wordRef={openingNameDefterRef} word="DEFT3R" className="opening-text-token opening-name-defter" />
            </p>
            <p className="opening-brand-assistant">
              <span ref={openingAssistantTextRef} className="opening-text-token opening-assistant-token">Toplantı asistanı</span>
            </p>
          </div>
          <div className="opening-start-credit-wrap">
            <p ref={openingPoweredRef} className="opening-brand-powered" aria-label="T3AI tarafından geliştirildi"><span className="opening-heart-light" aria-hidden="true">🤎</span><span className="opening-heart-dark" aria-hidden="true">🤍</span></p>
          </div>
          {introReady && !introStarted && (
            <button type="button" className="opening-start-btn" onClick={startIntro}>
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
    <div className="meeting-mascot" role="img" aria-label="Açılıp toplantı notlarını yazan T3AI DEFT3R maskotu">
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
