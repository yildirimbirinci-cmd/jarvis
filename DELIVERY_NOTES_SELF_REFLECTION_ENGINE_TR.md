# Jarvis Self Reflection Engine

Bu güncelleme Jarvis'in teknik komut beklemeden doğal kullanıcı geri bildirimini kendini geliştirme araştırmasına dönüştürmesini sağlar.

## Yeni davranışlar

- "Bugün biraz yavaşsın" -> performans araştırması
- "Kendini çok tekrar ediyorsun" -> tekrar araştırması
- "Bazen konuyu kaçırıyorsun" -> bağlam araştırması
- "Eskisi kadar doğal konuşmuyorsun" -> diyalog kalitesi araştırması
- "Konuşurken bazen takılıyorsun" -> ses kararlılığı araştırması

Jarvis geri bildirimi günlük dille kabul eder, uygun araştırmayı başlatır, sonucu bildirir ve açık kullanıcı onayı olmadan kod değiştirmez.

## Değişen dosyalar

- core/assistant.py
- core/self_improvement_research.py
- core/self_reflection_engine.py
- tests/test_self_improvement_research.py

## Doğrulama

- Python derleme kontrolü geçti.
- Odaklı testler: 22 passed.
