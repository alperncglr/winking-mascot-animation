# Canlı diarization yan servisi

Bu servis A4000 üzerinde ana FastAPI uygulamasından ayrı bir ortamda çalışır.
Her WebSocket bağlantısı bağımsız bir diart çevrimiçi kümeleme oturumudur; bu
sayede birden fazla toplantının konuşmacı durumu birbirine karışmaz.

```bash
bash services/live_diarization/setup_offline.sh
.venv-diart/bin/python services/live_diarization/diart_server.py --model-root models/pyannote-speaker-diarization-community-1 --check
.venv-diart/bin/python services/live_diarization/diart_server.py --host 127.0.0.1 --port 2710 --model-root models/pyannote-speaker-diarization-community-1
.venv/bin/python services/live_diarization/probe.py runtime/meetings/1/audio/chunk_000001.wav --url ws://127.0.0.1:2710
```

Ana uygulamaya ham 16 kHz mono PCM16 gelir. Ana uygulama aynı çerçeveleri bu
servise iletir. Servis yaklaşık her 500 ms'de bir anonim konuşmacı zaman
aralıkları döndürür. `speaker_0`, `speaker_1` gibi kimlikler yalnızca ilgili
WebSocket bağlantısının, dolayısıyla ilgili toplantının ömrü boyunca geçerlidir.

Servis yalnızca `127.0.0.1:2710` üzerinde dinlemelidir; toplantı odası
bilgisayarlarının bu porta erişmesi gerekmez.

A4000 air-gapped kurulumunda ana .venv değiştirilmez. diart ve Rx wheel
dosyaları diart_offline dizinine, resmi community-1 model deposu ise
models/pyannote-speaker-diarization-community-1 dizinine aktarılır.

İnternetli Windows makinede aynı Hugging Face hesabıyla community-1 kullanım
koşulları kabul edilir, hf auth login çalıştırılır ve model indirilir:

    python services/live_diarization/download_community_model.py

Araç token değerini kaydetmez veya yazdırmaz. İndirilen dosyalar için
manifest.sha256.json bütünlük manifesti üretir.

A4000'e aktarım tamamlandıktan sonra model dosyaları internet erişimi olmadan
manifest ile doğrulanır:

    python services/live_diarization/download_community_model.py --verify-only

Servis, yalnız kullanılmayan mikrofon, dosya ve diart WebSocket kaynakları için
pyannote 4 ve torchaudio 2.11 compatibility shim uygular. PushAudioSource ağdan
gelen PCM16 verisini doğrudan işler. Segmentation ve embedding modelleri yerel
community-1 dizininden yüklenir; runtime sırasında Hugging Face erişimi yapılmaz.

Canary testi tamamlandıktan sonra systemd servisi kurulur:

    sudo install -m 0644 services/live_diarization/meeting-scribe-diarization.service /etc/systemd/system/meeting-scribe-diarization.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now meeting-scribe-diarization.service

Servis yalnız 127.0.0.1:2710 üzerinde dinler. Durum ve loglar:

    sudo systemctl status meeting-scribe-diarization.service --no-pager
    sudo journalctl -u meeting-scribe-diarization.service -n 100 --no-pager
