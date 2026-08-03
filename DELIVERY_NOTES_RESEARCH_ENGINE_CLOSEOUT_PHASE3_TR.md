# Research Engine Closeout Phase 3

Bu paket yalnızca Research Engine kapanış kapsamındadır. Yeni modül eklemez.

## Düzeltilenler

- Tamamlanan veya başarısız araştırmalar için kalıcı bildirim durumu eklendi.
- Bildirimler yalnızca `pending` görevler için oluşturulur; uygulama yeniden açıldığında aynı araştırma için tekrar tekrar bildirim üretilmez.
- Kullanıcı araştırma sonucunu açtığında yalnızca o araştırmaya ait bildirim okunmuş sayılır.
- Bildirim deposuna tek bildirimi okundu işaretleme ve okunmamış bildirimleri listeleme desteği eklendi.
- Araştırma başarısızlığı artık ham teknik hata dökmek veya kök neden uydurmak yerine belirsizliği ve sonraki güvenli adımı açıklar.
- Eski tamamlanmış araştırmalar `notification_state=none` kabul edilir ve geriye dönük toplu bildirim üretmez.

## Değişen dosyalar

- `core/assistant.py`
- `core/self_improvement_research.py`
- `core/notification_store.py`
- `tests/test_self_improvement_research.py`
- `tests/test_notification_store.py`

## Doğrulama

- Python derleme kontrolü geçti.
- Odaklı testler: 39 passed.

## Research Engine kapanışı için kalanlar

- İnternet araştırması için araştırma-bazlı açık izin ve kaynak kalite zinciri.
- Gerçek ölçüm talebini ilerideki Experiment Runner'a yalnızca sözleşme üzerinden devretme.
- Güncel kullanıcı deposunda tam proje regresyonu ve taban karşılaştırması.

Research Engine bu aşamada henüz tamamlandı olarak işaretlenmemelidir.
