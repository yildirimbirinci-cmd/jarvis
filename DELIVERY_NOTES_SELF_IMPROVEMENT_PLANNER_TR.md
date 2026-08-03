# Jarvis Self Improvement Planner

Bu güncelleme tamamlanmış kendini geliştirme araştırmalarını güvenli uygulama planlarına dönüştürür.

## Yeni davranışlar

- "Bu çözüm için plan hazırla", "İyileştirme planı hazırla" ve "Seçenekleri karşılaştır" komutlarını tanır.
- Araştırma tamamlanmadan plan üretmez.
- Üç çözüm yaklaşımını risk ve kapsam açısından karşılaştırır.
- Çalışma zamanı kanıtı varsa küçük ve hedefli müdahaleyi önerir.
- Kanıt yetersizse önce ölçüm eklemeyi önerir.
- Uygulama kapsamını, worktree adımlarını ve doğrulama planını açıklar.
- Patch üretmez, dosya değiştirmez ve ayrıca kullanıcı onayı ister.
- Hazırlanan planı araştırma günlüğüne kalıcı olarak kaydeder.

## Değişen dosyalar

- core/assistant.py
- core/self_improvement_research.py
- tests/test_self_improvement_research.py

## Doğrulama

- Python derleme kontrolü geçti.
- Odaklı testler: 17 passed.
