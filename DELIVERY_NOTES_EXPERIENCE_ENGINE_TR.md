# Jarvis Experience Engine Güncellemesi

## Değişen dosyalar

- `core/assistant.py`
- `core/self_improvement_research.py`
- `core/self_improvement_experience.py` (yeni)
- `tests/test_self_improvement_research.py`

## Yeni davranış

- Tamamlanan araştırmalar kalıcı deneyim kaydına dönüşür.
- Deneyim sonucu `henüz denenmedi`, `işe yaradı`, `kısmen işe yaradı` veya `işe yaramadı` olarak saklanır.
- Kullanıcının doğal sonucu kaydedilir: `Bu çözüm işe yaradı`, `Biraz düzeldi`, `İşe yaramadı`.
- Yeni benzer şikâyette geçmiş deneyimler kategori, metin benzerliği ve gerçek sonuçla sıralanır.
- Başarılı çözüm karar desteği olarak kullanılır.
- Başarısız çözüm körü körüne yeniden önerilmez.
- `Daha önce ne öğrendin?` ve `Geçmiş deneyimlerini göster` komutları deneyim özetini verir.
- Kod değişikliği için açık onay gereksinimi korunur.

## Doğrulama

- Python derleme kontrolü geçti.
- Odaklı testler: `26 passed`.
