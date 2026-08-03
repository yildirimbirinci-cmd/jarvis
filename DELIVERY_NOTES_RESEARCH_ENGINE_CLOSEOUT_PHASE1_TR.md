# Research Engine Kapanış - Aşama 1

Bu paket yalnızca Research Engine kapanışına odaklanır.

Eklenenler:
- "Araştırmayı durdur / iptal et" doğal komutları.
- "Araştırmayı baştan başlat / yeniden araştır" doğal komutları.
- İptal edilen arka plan görevinin daha sonra sonucu ezmesini önleyen aktif görev kontrolleri.
- Yinelenen çalışma zamanı kanıtlarının dosya/sembol/kategori bazında birleştirilmesi.
- Aynı kanıtın farklı tekrar sayılarında görünmesi halinde en güçlü kaydın korunması.
- İptal edilen araştırmada hiçbir dosyanın değiştirilmediğini açıkça bildiren cevaplar.

Doğrulama:
- Python derleme kontrolü geçti.
- Research Engine + Time Budget odaklı testler: 36 passed.

Bu paket Research Engine'i tamamen kapatmaz. Kalan kapanış işleri:
- Aynı anda birden fazla araştırmanın kimlik bazlı yönetimi.
- Bildirimlerin uygulama yeniden açıldığında tek seferlik ve okunmuş durumuyla korunması.
- İnternet araştırması izin/kaynak kalite zinciri.
- Gerçek ölçüm deneyi talebi ve Experiment Runner'a kontrollü devir.
- Tam proje regresyon testi ve güncel taban karşılaştırması.
