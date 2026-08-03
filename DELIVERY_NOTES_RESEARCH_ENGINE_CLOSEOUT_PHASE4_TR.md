# Research Engine Closeout Phase 4

Bu paket yalnızca Research Engine'in kanıt ve dış kaynak izin zincirini tamamlar.

## Eklenenler
- Yerel çalışma zamanı, yerel kod ve dış kaynak kanıtları ayrı alanlarda saklanır.
- Dış araştırma görev bazlı açık izin olmadan çalışmaz.
- Kullanıcı dış kaynağı reddederse araştırma yalnızca yerel kanıtla devam eder.
- Dış kaynakların kalite etiketi tutulur: yüksek, orta, belirsiz.
- Yerel ve dış bulgular arasındaki çelişkiler ayrı kaydedilir.
- Dış kaynaklar yerel kanıt veya kod değişikliği onayı sayılmaz.
- Teknik rapor kanıt katmanlarını ayrı başlıklarla gösterir.

## Doğal komutlar
- "Bu araştırma için internet gerekir mi?"
- "Bu araştırma için internete izin veriyorum."
- "Bu araştırmada internete çıkma."
- "Yalnızca kendi loglarını kullan."
- "Kanıt kaynaklarını göster."

## Doğrulama
- Python derleme kontrolü geçti.
- Research Engine odaklı testler: 41 passed.
- Tam proje regresyonu henüz bu paket kapsamında çalıştırılmadı.
