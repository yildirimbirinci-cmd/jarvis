# Research Engine — Son Kapanış Doğrulaması

Bu paket yeni özellik eklemez. Research Engine'in `COMPLETE` ilan edilebilmesi için kalan son şartı doğrular.

## Kurulum

ZIP içindeki `tools/research_engine_closeout_validate.py` dosyasını proje kökündeki `tools` klasörüne kopyalayın.

## Çalıştırma

Proje sanal ortamı açıkken proje kökünde:

```powershell
python .\tools\research_engine_closeout_validate.py
```

## Yaptığı kontroller

1. `core/assistant.py` ve `core/self_improvement_research.py` derlemesi.
2. Research Engine odaklı testlerin çalıştırılması.
3. Tüm `pytest` test paketinin çalıştırılması.
4. Sonucun önceki doğrulanmış `1373 passed, 7 skipped` baseline ile karşılaştırılması.
5. `research_engine_closeout_report.json` raporunun oluşturulması.

Script yalnızca tüm kontroller başarılıysa `Research Engine durumu: COMPLETE` yazar.
