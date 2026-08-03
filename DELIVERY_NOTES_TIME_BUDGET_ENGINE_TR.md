# Jarvis Time Budget and Scope Engine

## Eklenenler
- Doğal dilde süre tahmini: "Bunu yapman ne kadar sürer?"
- En iyi / muhtemel / kötü durum süre aralığı
- Kullanıcı zaman bütçesi: "1 saatin var", "45 dakikada bitir"
- Süreye göre üç strateji:
  - tam kapsam
  - minimum güvenli teslimat
  - teşhis ve uygulanabilir plan
- Şimdi gerekli / sonraya bırakılacak / gereksiz iş ayrımı
- Derleme, kritik test, geri alma ve onay güvenlik sınırları
- Kalıcı zaman planı kaydı: diagnostics/time_budget.json

## Değişen dosyalar
- core/assistant.py
- core/time_budget_engine.py
- tests/test_time_budget_engine.py

## Doğrulama
- Python derleme kontrolü geçti.
- Odaklı testler: 32 passed.
