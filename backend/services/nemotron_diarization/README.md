# Nemotron 3 diarization canary

Bu servis üretimdeki Diart servisinin kullandığı WebSocket sözleşmesini korur:

- İstemci bağlanınca `{"type":"status","status":"ready"}` döner.
- Girdi 16 kHz mono PCM16 binary frame'lerdir.
- İstemci `{"eof":1}` ile oturumu kapatır.
- Çıktı `processed_until_sec` ve `speaker_0` biçimindeki `turns` listesidir.

Model bir kez GPU'ya yüklenir. Her bağlantı kendi audio buffer'ını ve Nemotron
speaker cache'ini taşır; toplantıların konuşmacı durumları birbirine karışmaz.
GPU forward çağrıları tek model üzerinde sıraya alınır. Canary portu yalnızca
localhost'a açılır; üretimdeki `127.0.0.1:2710` Diart servisi değiştirilmez.

## Paketleri internetli Linux makinede hazırla

Model kullanımı Open Model Development Watch License 1.1 koşullarına tabidir.

```bash
python3 -m venv .venv-download
.venv-download/bin/pip install 'huggingface-hub>=0.34'
.venv-download/bin/python services/nemotron_diarization/download_model.py

sudo docker build \
  -t nemotron-diarization:canary-20260924 \
  services/nemotron_diarization
sudo docker save \
  -o deploy/nemotron-diarization-image-20260924.tar \
  nemotron-diarization:canary-20260924
tar -czf deploy/nemotron-diarization-model-20260924.tgz \
  -C models Nemotron-3-Diarization
```

Docker build, Nemotron desteğini içeren Transformers commit'ine sabitlenmiştir.
Model indirme aracı da model deposunun belirli revision'ını kullanır.

## A4000 canary kurulumu

Önce mevcut servisleri ve boş VRAM'i kontrol et. Üretim servisini durdurma.

```bash
sudo docker load -i /tmp/nemotron-diarization-image-20260924.tar
mkdir -p /home/bt/models
tar --no-same-owner -xzf \
  /tmp/nemotron-diarization-model-20260924.tgz \
  -C /home/bt/models

sudo docker rm -f nemotron-diarization-canary 2>/dev/null || true
sudo docker run -d \
  --name nemotron-diarization-canary \
  --gpus all \
  --restart no \
  -p 127.0.0.1:2712:2710 \
  -v /home/bt/models/Nemotron-3-Diarization:/models/Nemotron-3-Diarization:ro \
  -e NEMOTRON_MODEL_PATH=/models/Nemotron-3-Diarization \
  -e NEMOTRON_STREAMING_MODE=low_latency \
  -e NEMOTRON_DTYPE=float16 \
  nemotron-diarization:canary-20260924

sudo docker logs --tail 100 nemotron-diarization-canary
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv
```

`Model ready` ve `Listening on ws://0.0.0.0:2710` görülmeden test yapma.

## Aynı sesle A/B testi

Tek servis testi:

```bash
cd /home/bt/meeting-scribe
.venv/bin/python services/live_diarization/probe.py \
  runtime/meetings/1/audio/chunk_000001.wav \
  --url ws://127.0.0.1:2712
```

Diart 2710 ve Nemotron 2712 karşılaştırması:

```bash
.venv/bin/python services/nemotron_diarization/compare_services.py \
  runtime/meetings/1/audio/chunk_000001.wav
```

Karar yalnızca speaker sayısına göre verilmemeli. Türkçe gerçek toplantılarda
konuşmacı değişimlerinin zamanlaması, aynı konuşmacının bölünmesi, farklı
konuşmacıların birleşmesi ve overlap aralıkları elle karşılaştırılmalıdır.

## Önemli sınır

Nemotron, aynı anda konuşan kişileri ayrı speaker activity kanallarıyla işaretler.
Fakat mevcut Whisper tek bir mono metin akışı üretir. Bu nedenle diarization tek
başına overlap sırasında her kelimenin hangi kişiye ait olduğunu kusursuz biçimde
çözmez. Bunun için ileride speaker-conditioned/multi-talker ASR gerekir.
