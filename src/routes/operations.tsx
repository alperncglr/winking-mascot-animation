import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Activity, ArrowLeft, LockKeyhole, LogOut, Moon, Sun } from "lucide-react";
import "../operations.css";

export const Route = createFileRoute("/operations")({
  head: () => ({ meta: [{ title: "T3AI DEFT3R — Operasyon" }, { name: "robots", content: "noindex,nofollow" }] }),
  component: Operations,
});

type Session = {
  session_id: string; meeting_id: number; title: string | null; state: string;
  browser_connection: "connected" | "closed"; audio_stream: "waiting" | "receiving" | "stalled" | "disconnected";
  language: string; detected_language: string | null; meeting_status: string | null;
  received_sec: number; processed_sec: number | null; finalized_sec: number | null;
  diarized_sec: number | null; lag_sec: number | null; audio_idle_sec: number | null;
  queue_ms: number | null; inference_ms: number | null; request_id: number | null;
  whisper_state: string; diarization_state: string; worker: string | null;
  transcript_queue: number; diarization_queue: number; diarization_dropped_frames: number;
  last_audio_at: string | null; last_whisper_at: string | null;
  last_provisional_at: string | null; last_final_at: string | null;
  last_forwarded_at: string | null; error: string | null; telemetry: boolean;
};
type MeetingRow = { id: number; title: string; status: string; updated_at: string };
type Snapshot = {
  generated_at: string; process_id: number; sessions: Session[]; recent_sessions: Session[];
  waiting_whisper: number; running_whisper: number;
  gpu: { sampled_at: string | null; error: string | null; devices: {
    index: string; uuid: string; name: string; utilization_pct: number | null;
    used_mib: number | null; total_mib: number | null;
  }[] };
  active_meetings: MeetingRow[]; processing_meetings: MeetingRow[];
  completed_meetings: MeetingRow[]; failed_meetings: MeetingRow[];
};
const API = (import.meta.env["VITE_MEETING_SCRIBE_API_URL"] ?? "").replace(/\/$/, "");
const sec = (n: number | null) => n === null ? "—" : `${n.toFixed(1)} sn`;
const ms = (n: number | null) => n === null ? "—" : `${Math.round(n)} ms`;
const stamp = (s: string | null) => s ? new Date(s).toLocaleTimeString("tr-TR") : "—";
const stages: Record<string, string> = {
  connecting: "Bağlanıyor", connected: "Bağlı", streaming: "Ses alınıyor", draining: "Sonuçlar bekleniyor",
  closed: "Kapandı", queued: "Sırada", running: "Çözümleniyor", completed: "İş tamamlandı",
  processing: "İşleniyor", error: "Hata", timeout: "Zaman aşımı",
};
const meetingStatuses: Record<string, string> = {
  recording: "Kaydediliyor", recorded: "Döküm bekliyor", refining: "Döküm işleniyor",
  refined: "Döküm hazır", summarizing: "Özet işleniyor", summary_pending: "Özet bekliyor",
  summarized: "Özet hazır", finished: "Tamamlandı", recording_failed: "Kayıt hatası",
  refine_failed: "Döküm hatası", summary_failed: "Özet hatası",
};

function MeetingGroup({ title, description, meetings, empty }: {
  title: string; description: string; meetings: MeetingRow[]; empty: string;
}) {
  return <section className="ops-card">
    <h2>{title} · {meetings.length}</h2>
    <p className="ops-hint">{description}</p>
    {!!meetings.length && <div className="ops-table-scroll"><table><thead><tr>
      <th>Toplantı</th><th>Durum</th><th>Son güncelleme</th>
    </tr></thead><tbody>{meetings.map(meeting => <tr key={meeting.id}>
      <td>#{meeting.id} · {meeting.title || "İsimsiz toplantı"}</td>
      <td>{meetingStatuses[meeting.status] || meeting.status}</td>
      <td>{new Date(meeting.updated_at).toLocaleString("tr-TR")}</td>
    </tr>)}</tbody></table></div>}
    {!meetings.length && <p className="ops-empty">{empty}</p>}
  </section>;
}

function Operations() {
  // The administrator secret exists only in memory; never in URL, browser storage or build env.
  const [secret, setSecret] = useState("");
  const [draft, setDraft] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [receivedAt, setReceivedAt] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const [filter, setFilter] = useState("");
  const [recent, setRecent] = useState(false);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!secret) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const poll = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch(`${API}/admin/monitor`, {
          headers: { "X-Admin-Key": secret }, signal: controller.signal, cache: "no-store",
          credentials: "omit",
        });
        if (disposed) return;
        if (response.status === 401 || response.status === 403) {
          setSecret(""); setSnapshot(null); setError("Yönetici anahtarı geçersiz veya erişim reddedildi.");
          return;
        }
        if (response.status === 503) throw new Error("Panel sunucuda etkin değil. MEETING_SCRIBE_ADMIN_KEY yapılandırılmalı (en az 32 karakter).");
        if (!response.ok) throw new Error(`İzleme verisi alınamadı (HTTP ${response.status}).`);
        const data: Snapshot = await response.json();
        if (!Array.isArray(data.sessions) || !data.gpu) throw new Error("Sunucudan beklenmeyen yanıt alındı.");
        if (!disposed) { setSnapshot(data); setReceivedAt(Date.now()); setError(""); }
      } catch (err) {
        if (!disposed) setError(err instanceof Error && err.name !== "AbortError" ? err.message : "İstek zaman aşımına uğradı. Son veriler güncel değil.");
      } finally {
        clearTimeout(timeout);
        if (!disposed) timer = setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); };
  }, [secret]);

  const stale = !!snapshot && (!!error || clock - receivedAt > 8000);
  const rows = (recent ? snapshot?.recent_sessions : snapshot?.sessions) ?? [];
  const filtered = rows.filter(row => `${row.meeting_id} ${row.title ?? ""}`.toLocaleLowerCase("tr").includes(filter.toLocaleLowerCase("tr")));
  const maxLag = snapshot?.sessions.reduce<number | null>((max, row) => row.lag_sec === null ? max : Math.max(max ?? 0, row.lag_sec), null) ?? null;

  return <main className={`ops ${dark ? "ops-dark" : ""}`}>
    <div className="ops-wrap">
      <header className="ops-header">
        <div><a href="/" className="ops-back"><ArrowLeft size={15} /> Deftere dön</a>
          <div className="ops-eyebrow">T3AI DEFT3R / OPERASYON</div>
          <h1>Toplantı kontrol merkezi</h1><p>Ses akışı, işlem süresi ve gecikme. Tek ekranda.</p></div>
        <div className="ops-actions">
          <button aria-label={dark ? "Açık tema" : "Koyu tema"} onClick={() => setDark(!dark)}>{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
          {secret && <button onClick={() => { setSecret(""); setSnapshot(null); setDraft(""); setError(""); }}><LogOut size={16} /> Çıkış</button>}
        </div>
      </header>

      {error && <div role="alert" className="ops-alert">{error}</div>}
      {!secret ? <section className="ops-card ops-login">
        <LockKeyhole size={28} /><h2>Yönetici erişimi</h2>
        <p>Normal toplantı anahtarından farklı olan yönetici anahtarını gir. Anahtar bu sekmede tutulur; kaydedilmez.</p>
        <form onSubmit={e => { e.preventDefault(); setError(""); setSecret(draft); setDraft(""); }}>
          <label htmlFor="admin-secret">Yönetici anahtarı</label>
          <input id="admin-secret" type="password" autoComplete="off" required minLength={32} value={draft} onChange={e => setDraft(e.target.value)} />
          <button className="ops-primary" type="submit">İzlemeyi aç</button>
        </form>
        <small>HTTPS üzerinden kullan. Bu panel toplantıları durdurmaz veya değiştirmez.</small>
      </section> : !snapshot ? <section className="ops-card" role="status">İzleme verileri alınıyor…</section> : <>
        <div className="ops-freshness" role="status"><span className={`ops-dot ${stale ? "ops-bad" : ""}`} />
          {stale ? "Veri güncel değil — canlı durum olarak yorumlama" : "Canlı izleme · 2 saniyede bir yenilenir"}
          <span>Son yanıt {stamp(snapshot.generated_at)}</span>
        </div>
        <section className="ops-stats" aria-label="Genel durum">
          {[['Aktif ses oturumu', snapshot.sessions.length], ['Whisper sırasında', snapshot.waiting_whisper],
            ['Whisper işleniyor', snapshot.running_whisper], ['En büyük ses farkı', sec(maxLag)]].map(([label, value]) =>
              <div className="ops-card" key={label}><span>{label}</span><strong>{value}</strong></div>)}
        </section>
        <section className="ops-gpus" aria-label="GPU durumu">
          {snapshot.gpu.devices.map(gpu => <article className="ops-card" key={gpu.uuid}>
            <div className="ops-card-heading"><h2>GPU {gpu.index} · {gpu.name}</h2><Activity size={18} /></div>
            <div className="ops-gpu-value">{gpu.utilization_pct === null ? "—" : `%${gpu.utilization_pct}`} <small>hesaplama kullanımı</small></div>
            <progress aria-label={`GPU ${gpu.index} kullanım`} max={100} value={gpu.utilization_pct ?? 0} />
            <p>{gpu.used_mib === null ? "—" : (gpu.used_mib / 1024).toFixed(1)} / {gpu.total_mib === null ? "—" : (gpu.total_mib / 1024).toFixed(1)} GiB bellek · {stamp(snapshot.gpu.sampled_at)}</p>
          </article>)}
          {snapshot.gpu.error && <div className="ops-card">GPU ölçümü alınamıyor. Toplantı ölçümleri izlenmeye devam eder.</div>}
        </section>

        <MeetingGroup title="Devam eden toplantılar" description="Kayıt durumu devam eden toplantılar. Ses bağlantısının ayrıntıları aşağıdaki canlı oturumlarda görünür." meetings={snapshot.active_meetings} empty="Şu anda kaydı devam eden toplantı yok." />
        <MeetingGroup title="Döküm ve özet işlenenler" description="Kaydı bitmiş, döküm veya özet aşamasındaki toplantılar. Bu durumlar veritabanından gelir; işçinin hâlâ çalıştığını tek başına kanıtlamaz." meetings={snapshot.processing_meetings} empty="İşlem bekleyen veya işlenen toplantı yok." />
        <MeetingGroup title="Tamamlanan toplantılar" description="Dökümü ve özeti tamamlanmış son 100 toplantı." meetings={snapshot.completed_meetings} empty="Henüz tamamlanan toplantı yok." />
        <MeetingGroup title="İşlem hataları" description="Kayıt, döküm veya özet aşamasında hata durumuna düşen toplantılar." meetings={snapshot.failed_meetings} empty="Hata durumunda toplantı yok." />
        <section className="ops-card">
          <div className="ops-toolbar"><h2>Canlı ses oturumları</h2><div className="ops-actions">
            <button aria-pressed={!recent} onClick={() => setRecent(false)}>Aktif</button>
            <button aria-pressed={recent} onClick={() => setRecent(true)}>Son kapananlar</button>
            <input aria-label="Toplantı numarası veya adıyla ara" placeholder="ID veya toplantı adı ara…" value={filter} onChange={e => setFilter(e.target.value)} />
          </div></div>
          <p className="ops-hint">Ses farkı = backend’e ulaşan ses − Whisper’ın işlediği ses. Tarayıcı/ağ gecikmesini içermez; final metin gecikmesi değildir. Sıra sayısı yalnızca bu backend’in oturumlarını kapsar.</p>
          <p className="ops-hint">Bağlantı ve ses durumu sunucunun gözlemidir. Tarayıcının mikrofon izni ile donanım durumunu bu panel uzaktan doğrulayamaz.</p>
          <div className="ops-table-scroll"><table><thead><tr>
            <th>Toplantı / oturum</th><th>Akış</th><th>Dil</th><th>Ses / işlenen</th><th>Ses farkı</th><th>Sıra / çözümleme</th><th>Whisper</th><th>Ayrıntı</th>
          </tr></thead><tbody>{filtered.map(row => <tr key={row.session_id}>
            <td><strong>#{row.meeting_id} · {row.title || "İsimsiz toplantı"}</strong><small>{row.session_id.slice(0, 10)} · {row.meeting_status}</small></td>
            <td><span className={`ops-badge ${row.browser_connection !== "connected" ? "ops-warning" : ""}`}>{row.browser_connection === "connected" ? "Bağlantı kurulu" : "Bağlantı kapalı"}</span>
              <small>{row.audio_stream === "receiving" ? "Ses sunucuya ulaşıyor" : row.audio_stream === "stalled" ? "Ses akışı durdu" : row.audio_stream === "waiting" ? "Ses bekleniyor" : "Ses bağlantısı kapalı"}</small>
              <small>Son ses {sec(row.audio_idle_sec)} önce · {stages[row.state] || row.state}</small></td>
            <td>{row.language === "mixed" ? "Otomatik" : row.language}<small>Algılanan: {row.detected_language || "—"}</small></td>
            <td>{sec(row.received_sec)}<small>İşlenen: {sec(row.processed_sec)}</small></td>
            <td className={row.lag_sec !== null && row.lag_sec > 5 ? "ops-danger" : ""}>{sec(row.lag_sec)}
              {!row.telemetry && <small>Bridge ölçümü yok</small>}</td>
            <td>{ms(row.queue_ms)}<small>Çözümleme: {ms(row.inference_ms)}</small></td>
            <td>{stages[row.whisper_state] || row.whisper_state}<small>İstek #{row.request_id ?? "—"}</small></td>
            <td><details><summary>Ayrıntı</summary><dl className="ops-details">
              <dt>Model işleyicisi</dt><dd>{row.worker || "—"}</dd>
              <dt>Konuşmacı ayrımı</dt><dd>{stages[row.diarization_state] || row.diarization_state} · {sec(row.diarized_sec)}</dd>
              <dt>Kesinleşen metin konumu</dt><dd>{sec(row.finalized_sec)} (sessizlikte ilerlemeyebilir)</dd>
              <dt>Son geçici / final</dt><dd>{stamp(row.last_provisional_at)} / {stamp(row.last_final_at)}</dd>
              <dt>Son Whisper yanıtı</dt><dd>{stamp(row.last_whisper_at)}</dd>
              <dt>Tarayıcıya son gönderim</dt><dd>{stamp(row.last_forwarded_at)}</dd>
              <dt>Metin / diarization kuyruğu</dt><dd>{row.transcript_queue} / {row.diarization_queue}</dd>
              <dt>Atlanan diarization paketi</dt><dd>{row.diarization_dropped_frames}</dd>
              <dt>Son hata kodu</dt><dd>{row.error || "Yok"}</dd>
            </dl></details></td>
          </tr>)}</tbody></table></div>
          {!filtered.length && <p className="ops-empty">{filter ? "Aramayla eşleşen toplantı yok." : recent ? "Bu süreçte henüz kapanan oturum yok." : "Şu anda izlenen canlı ses oturumu yok."}</p>}
        </section>
        <footer className="ops-hint">Salt okunur · Toplantı içerikleri gösterilmez · Backend PID {snapshot.process_id} · Tek backend süreci kapsamı; yeniden başlatmada canlı ölçümler sıfırlanır.</footer>
      </>}
    </div>
  </main>;
}
