# Jarvis Windows self-hosted runner

Bu güncelleme Jarvis uygulamasının kabul testini harici bir başlatıcıya taşımaz. GitHub Actions yalnızca depodaki gerçek testleri ve yerel Ollama servisini çağırır.

## Runner etiketleri

Runner GitHub üzerinde şu etiketlerle kayıtlı olmalıdır:

- `self-hosted`
- `windows`
- `x64`
- `jarvis`

`jarvis` etiketi özellikle eklenmelidir; böylece bu iş başka bir Windows runner üzerinde yanlışlıkla çalışmaz.

## Gerekli yerel bileşenler

- Windows 10/11
- Python 3.11
- GitHub Actions runner servisi
- Ollama, `http://127.0.0.1:11434` adresinde çalışır durumda
- Varsayılan modeller: `qwen2.5:7b` ve `qwen2.5-coder:7b`

## Çalıştırma

GitHub > Actions > **Jarvis Windows Ollama Acceptance** > **Run workflow**.

Bu workflow yalnızca manuel başlatılır. Önce Ollama ve model envanterini doğrular, sonra öz-geliştirme güvenlik testlerini, gerçek model probunu ve tercihe göre tam pytest paketini çalıştırır.

## Kabul ölçütü

Aşağıdaki adımların tamamı yeşil olmadan Jarvis'in gerçek ortamda kendi kendini geliştirebildiği kabul edilmez:

1. Derleme
2. Odaklı öz-geliştirme ve geri alma testleri
3. Gerçek sohbet modeli yanıtı
4. Gerçek kod modeli yanıtı
5. Tam depo regresyonu
6. Uygulama içinden oluşturulan kabul raporu ve destek paketi
