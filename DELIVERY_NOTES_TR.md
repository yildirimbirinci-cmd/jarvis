# Jarvis Windows Uçtan Uca Kabul ve Stabilizasyon Teslimi

**Tarih:** 1 Ağustos 2026

Bu arşiv, önceki doğrulanmış geliştirme ZIP'lerinin güncel birleşimidir. Önceki ZIP'leri bunun üzerine yeniden açmayın. Arşiv yalnızca değişen/yeni kaynak ve test dosyalarını içerir; Piper, modeller, sanal ortam ve değişmeyen dosyaları tekrar taşımaz.

## Bu teslim ne ekliyor?

Jarvis uygulamasına **Kabul ve Stabilizasyon** sekmesi eklenir. Kabul testi uygulamanın içinden çalışır; harici `.bat` veya PowerShell kabul çalıştırıcısına bağımlı değildir.

### Hızlı kabul testi

Şunları denetler:

1. Python ve uygulama çalışma ortamı
2. Kaynakların Python derleme kontrolü
3. Sohbet modeli ile kod modelinin ayrı ve yerel olarak hazır olması
4. Kesilebilir konuşma turu, görev ve TTS sözleşmesi
5. Çalışma zamanı gözlem bağlantıları
6. İzinli geçici alanda kopyalama, yeniden adlandırma, geri alma ve kök dışı erişim reddi
7. Geçici bir Python CLI projesi oluşturma, build ve pytest
8. Kendi kodu için checkpoint, kurtarma, undo/redo ve onay sınırı
9. İnternet araştırmasının ayrı izin anahtarıyla kapalı/açık yönetilmesi

### Tam Windows kabul testi

Hızlı testlere ek olarak:

1. Temiz Python sürecinde uygulama importu
2. Tam depo testleri
3. GUI smoke testi
4. Gerçek Windows mikrofon, ses çıkışı ve TTS hazırlığı
5. Kullanıcının test sesini gerçekten duyduğuna ilişkin fiziksel onay

Fiziksel ses onayı testten önce işaretlenemez. Jarvis önce Windows ses yolunu sınar, ardından test sesini duyup duymadığınızı sorar. “Hayır” yanıtı raporu başarısız yapar; “Evet” yanıtı rapora atomik olarak kaydedilir.

## İptal ve ilerleme

Kabul testi ayrı, kesilebilir bir Qt işçisinde çalışır. Uzun test, import veya GUI alt süreçleri iptal edildiğinde alt süreç sonlandırılır; yalnızca arayüzde “iptal edildi” yazmakla yetinilmez. Her adımın başlangıcı, sonucu ve geçen süresi ekranda gösterilir.

## Güvenlik

- Kabul testi kaynak kodu değiştirmez.
- Dosya sistemi testi yalnızca kabul çalışmasının geçici sandbox klasöründe yapılır.
- İnternet araştırması başlatılmaz.
- Ollama adresi yalnızca yerel `localhost/127.0.0.1/::1` olabilir.
- Parola, token, API anahtarı ve Authorization değerleri rapor detaylarında ve kanıt alanlarında maskelenir.
- Rapor ve destek paketi atomik olarak yazılır.
- Fiziksel ses onayı yalnızca en son rapora ve başarılı donanım testinden sonra verilebilir.

## Yerel rapor konumu

Windows'ta raporlar varsayılan olarak şurada tutulur:

```text
%LOCALAPPDATA%\ArtmachAssistant\logs\acceptance
```

Her çalışmada:

```text
runs\E2E-XXXXXXXXXXXX\report.json
runs\E2E-XXXXXXXXXXXX\support_bundle.zip
e2e_latest.json
```

oluşturulur.

## Kurulum yolu

Arşivi aşağıdaki **iç proje klasörüne** açın:

```text
C:\Users\yildi\Desktop\JARVIS_PROJECT\Artmach_Asistant_Program\artmach_assistant
```

Bir üst klasöre açmayın.

## PowerShell uygulama komutları

```powershell
$root = "C:\Users\yildi\Desktop\JARVIS_PROJECT\Artmach_Asistant_Program"
$project = Join-Path $root "artmach_assistant"
$python = Join-Path $root ".venv\Scripts\python.exe"

Set-Location $project
chcp 65001 > $null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Önceki arayüz bağlantıları; zaten kuruluysa hiçbir şeyi tekrar eklemez.
& $python .\tools\apply_voice_gui_integration.py --project-root . --check
& $python .\tools\apply_voice_gui_integration.py --project-root . --apply

& $python .\tools\apply_project_development_ui.py --project-root . --check
& $python .\tools\apply_project_development_ui.py --project-root . --apply

# Yeni kabul ve stabilizasyon sekmesi.
& $python .\tools\apply_end_to_end_acceptance_ui.py --project-root . --check
& $python .\tools\apply_end_to_end_acceptance_ui.py --project-root . --apply

& $python -m py_compile .\app.py
& $python -m compileall -q .\config.py .\core .\tests .\tools
```

Ardından uygulamayı proje üst klasöründen başlatın:

```powershell
Set-Location $root
& $python -m artmach_assistant
```

## Kullanım

Arayüzde **Kabul ve Stabilizasyon** sekmesini açın.

Önce:

```text
Hızlı Kabul Testi
```

sonra:

```text
Tam Windows Kabul Testi
```

çalıştırın.

Sesli/yazılı komutlar da desteklenir:

```text
Hızlı sistem kabul testi.
Tam sistem kabul testi.
Son kabul raporunu göster.
```

## Bu ortamda doğrulananlar

- Yeni ve ilgili regresyon testleri: **65 geçti**
- Geniş regresyon grupları: **569 + 389 + 292 = 1.250 test geçti**
- `compileall`: başarılı
- Hızlı kabul çekirdeği, gerçek derleme, konuşma sözleşmesi, gözlem, dosya sandbox'ı ve yeni proje build/test adımlarıyla baştan sona başarılı çalıştı. Bu çalışmada yalnızca PySide6/Ollama envanteri fiziksel Linux ortamında olmadığı için çalışma ortamı ve model envanteri test sağlayıcısıyla verildi.
- Eski gerçek `app.py` kopyasında arayüz entegrasyonu uygulandı, ikinci uygulamada yinelenmedi ve geri alma sonrası dosya SHA-256 düzeyinde eski hâline döndü.

## Bu ortamda yapılamayan fiziksel doğrulama

Linux geliştirme ortamında aşağıdakiler bulunmadığından bunların başarı iddiası yapılmıyor:

- Windows 10 ses sürücüleri
- Logitech G635/G633s mikrofon
- Kullanıcının seçtiği gerçek hoparlör/monitör/Bluetooth çıkışı
- Yerel Piper Türkçe ses modeliyle fiziksel ses
- Gerçek Ollama modellerinin cevap kalitesi
- Gerçek PySide6 pencere görünümü

Bu teslimin amacı bu doğrulamaları artık Jarvis'in kendi içinden, raporlu ve iptal edilebilir biçimde çalıştırmaktır.

## Geri alma

Yalnızca kabul sekmesi bağlantısını kaldırmak için:

```powershell
& $python .\tools\apply_end_to_end_acceptance_ui.py --project-root . --revert
```

Yedek:

```text
.jarvis_backups\app.py.before_end_to_end_acceptance_ui
```

## Sonraki ve son ana teslim

Bu kabul çalışması Windows bilgisayarınızda çalıştırılıp rapor alındıktan sonra kalan tek ana teslim, **nihai temiz kaynak + kurulum/ilk açılış paketi + geri dönüş arşivi** olacaktır.
