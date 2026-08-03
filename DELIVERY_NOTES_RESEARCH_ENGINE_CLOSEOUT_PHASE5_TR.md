# Research Engine Closeout Phase 5

Bu paket Research Engine ile gelecekteki Experiment Runner arasındaki ölçüm talebi sözleşmesini tamamlar.

## Eklenen davranışlar

- Yerel kanıt kök nedeni doğrulamaya yetmezse araştırma yanlış biçimde tamamlanmış sayılmaz.
- Standart ve kalıcı bir deney/ölçüm talebi oluşturulur.
- Talebin sahibi açıkça `ExperimentRunner` olarak kaydedilir.
- Research Engine deneyi çalıştırmaz, patch üretmez ve gerçek kaynak dosyalarını değiştirmez.
- Aynı araştırma, deney türü ve hedef için yinelenen açık talep oluşturulmaz.
- Talep durumu `requested`, `accepted`, `running`, `completed`, `failed` veya `cancelled` olarak izlenebilir.
- Tamamlanan ölçümün sonuç referansı yeniden araştırma görevine bağlanır.
- `Ölçüm talebi ne durumda?`, `Hangi ölçümü bekliyorsun?` gibi doğal takip soruları desteklenir.

## Değişen dosyalar

- `core/assistant.py`
- `core/self_improvement_research.py`
- `tests/test_self_improvement_research.py`

## Doğrulama

- Python derleme kontrolü geçti.
- Research Engine ve bildirim odaklı testler: **49 passed**.
- Tam proje regresyonu bu pakette çalıştırılmadı. Research Engine ancak güncel depoda tam regresyon tamamlandıktan sonra `COMPLETE` sayılacaktır.
