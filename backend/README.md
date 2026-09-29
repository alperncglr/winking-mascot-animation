# T3AI DEFTER

Fiziksel toplantı odalarındaki mikrofon sesini merkezi NVIDIA A4000 sunucusuna
aktararak canlı anonim konuşmacılı metin, nihai transkript ve toplantı özeti
üreten şirket içi sistem.

## Mimari

- Toplantı odası bilgisayarı yalnızca HTTPS web arayüzünü açar, mikrofonu yakalar
  ve 16 kHz mono PCM16 sesi WebSocket ile A4000'e gönderir.
- Ana FastAPI uygulaması sesi dayanıklı 30 saniyelik WAV parçalarına yazar.
- `10.0.111.32:2700` üzerindeki Whisper WebSocket canlı taslak metni üretir.
- Ayrı Python ortamındaki yerel diart servisi aynı ses akışından çevrimiçi
  `speaker_0`, `speaker_1`, ... kümelerini üretir.
- FastAPI bu kümeleri ilk görülme sırasıyla `Kullanıcı 1`, `Kullanıcı 2`, ...
  olarak toplantı veritabanına bağlar. Aynı küme toplantı boyunca aynı etiketi
  korur; etiketler toplantılar arasında taşınmaz.
- Toplantı bitince A4000 üzerindeki faster-whisper `large-v3` gerçek kelime
  zamanlarını, pyannote community-1 konuşmacı aralıklarını üretir. Nihai
  konuşmacılı metin bu iki bağımsız çıktının zaman örtüşmesiyle hazırlanır.

Canlı etiketleme düşük gecikmeli çevrimiçi bir tahmindir. Diart yaklaşık ilk beş
saniyelik sesi biriktirirken ilk mesaj gecikebilir. Resmî çıktı her zaman toplantı
sonundaki tam kayıt üzerinde çalışan offline geçiştir.

## A4000 ana uygulama ortamı

Önerilen ortam Python 3.12'dir. Ana ortamda diart kurulmaz; burada güncel
pyannote 4 ve faster-whisper bulunur.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e '.[offline,diarization]'
cp .env.example .env
```

`.env` içinde şunlar doldurulur:

```dotenv
HF_TOKEN=hf_xxxxx
COMPANY_LLM_API_KEY=xxxxx
MEETING_SCRIBE_API_KEY=uzun-rastgele-bir-deger
```

Ana uygulama:

```bash
source .venv/bin/activate
python -m meeting_scribe
```

Varsayılan olarak `0.0.0.0:8000` üzerinde dinler. Toplantı odası tarayıcılarında
mikrofon izni için uygulama bir şirket içi HTTPS reverse proxy arkasından
sunulmalıdır. Yalnızca bu HTTPS adresi odalara açılır; 2700 ve 2710 doğrudan oda
bilgisayarlarına açılmaz.

## Canlı diart ortamı

Diart güncel offline pyannote sürümüyle aynı bağımlılık ortamını paylaşmadığı için
ayrı Python 3.10 conda ortamında çalışır:

```bash
conda env create -f services/live_diarization/environment.yml
conda activate meeting-scribe-diart
huggingface-cli login
python services/live_diarization/diart_server.py --host 127.0.0.1 --port 2710
```

Bu servis çoklu WebSocket oturumu kabul eder. Her bağlantının çevrimiçi kümeleme
durumu ayrıdır; farklı toplantı odalarının konuşmacıları birbirine karışmaz.

## Normal kullanıcı akışı

1. Arayüzde API anahtarı, başlık ve dil seçilir; toplantı oluşturulur.
2. `Canlı Yayını Başlat` mikrofonu açar. Ses aynı anda diske, 2700 Whisper'a ve
   yerel 2710 diart servisine gider.
3. Canlı ekran her konuşmayı sabit renkli `Kullanıcı N` sohbet balonu olarak
   gösterir.
4. `Kaydı Bitir` sesi kapatır ve offline faster-whisper + pyannote işlemini
   arka planda başlatır.
5. Nihai transkript hazır olunca `Özet Al` şirket içi LLM'den yapılandırılmış
   özet, karar ve aksiyonları alır.
6. `Toplantıyı Bitir` geçici sesleri ve canlı taslağı siler; nihai transkript,
   özet ve veritabanı kayıtları kalır.

## Dosya yaşam döngüsü

```text
runtime/meetings/{id}/audio/       geçici WAV parçaları + manifest
runtime/meetings/{id}/live_transcript.jsonl
archive/meetings/{id}/final_transcript.md
archive/meetings/{id}/summary.md
storage/meeting_scribe.sqlite3
```

Yarım kalan toplantıların geçici dosyaları varsayılan olarak 24 saat sonra
temizlenir. Offline transkripsiyon veya özet başarısız olursa ses korunur ve işlem
yeniden denenebilir.

## Testler

```bash
python -m pytest -q
```

Testler gerçek GPU modeli indirmeden zaman damgası bütünlüğünü, konuşmacı
örtüşmesini, toplantı içi sabit etiketleri, canlı konuşmacı takipçisini, kayıt
manifestini, veritabanını ve veri temizliğini doğrular.
