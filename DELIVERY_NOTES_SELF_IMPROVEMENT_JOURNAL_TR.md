# Jarvis Kendini Geliştirme Araştırma Günlüğü

## Eklenen davranışlar

- Her araştırma başlangıç, ilerleme ve tamamlanma adımlarını zaman damgalı günlükte saklar.
- Hipotezler desteklendi, elendi veya ölçüm bekliyor biçiminde kaydedilir.
- Tamamlanan araştırmalar `self_improvement_research_history.json` dosyasına arşivlenir.
- Yeni şikâyetlerde benzer tamamlanmış araştırmalar bulunur ve yeni göreve bağlanır.
- Kullanıcı `araştırma günlüğünü göster`, `hangi hipotezleri denedin` veya `neleri eledin` diyebilir.
- Günlük yalnızca öğrenme ve tanı kaydıdır; kod değişikliği onayı sayılmaz.

## Değişen dosyalar

- `core/assistant.py`
- `core/self_improvement_research.py`
- `tests/test_self_improvement_research.py`

## Doğrulama

- Python derleme kontrolü geçti.
- `pytest -q tests/test_self_improvement_research.py`: 13 passed.
