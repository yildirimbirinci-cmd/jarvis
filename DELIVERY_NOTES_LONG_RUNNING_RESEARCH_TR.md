# Jarvis Uzun Süreli Araştırma Akışı

## Değişen dosyalar
- `core/assistant.py`
- `core/self_improvement_research.py`
- `tests/test_self_improvement_research.py`

## Yeni davranış
- "Yavaşladığını hissediyorum, nedenlerini araştır" gibi doğal ifadeler tanınır.
- İlk cevap kısa kalır; ham teknik rapor gösterilmez.
- Araştırma `queued -> researching -> solution_found/failed` durumlarıyla kalıcı tutulur.
- İlerleme `%20`, `%55`, `%80`, `%100` aşamalarında kaydedilir.
- `Araştırma ne durumda?` yalnızca ilerlemeyi verir.
- `Ne buldun?` günlük dilde sonucu verir.
- `Teknik ayrıntıları göster` fonksiyon, dosya ve kanıt bilgilerini ayrı raporlar.
- Normal sohbet, kendi kodunu geliştirme ve ses işlemleri aynı kök nedenmiş gibi birleştirilmez.
- Kod değişikliği kullanıcı onayı olmadan yapılmaz.

## Doğrulama
```powershell
python -m py_compile .\core\assistant.py .\core\self_improvement_research.py
python -m pytest -q .\tests\test_self_improvement_research.py
```

Beklenen sonuç: `10 passed`.

## Kurulum
ZIP içeriğini proje köküne, klasör yapısını koruyarak kopyalayın ve mevcut dosyaların üzerine yazın.
