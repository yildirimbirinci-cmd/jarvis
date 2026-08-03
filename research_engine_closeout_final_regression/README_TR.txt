RESEARCH ENGINE FINAL REGRESYON KAPANISI

Kaynak doğrusu:
https://github.com/yildirimbirinci-cmd/jarvis

Bu paket yeni özellik veya kaynak kod değişikliği yapmaz. Phase 4 ve Phase 5 sonrasında eksik kalan güncel depo tam regresyonunu güvenli ve raporlu biçimde çalıştırır.

KURULUM / ÇALIŞTIRMA

1. ZIP'i proje köküne çıkarın:
   C:\Users\yildi\Desktop\artmach_assistant

2. Jarvis sanal ortamı açıkken çalıştırın:
   python .\research_engine_closeout_final_regression\install_research_engine_closeout_final_regression.py

Üretilen dosyalar proje üst klasöründedir:
- research_engine_closeout_final_regression_report.json
- research_engine_closeout_final_regression_pytest.log

ÖNEMLİ
- Script kaynak dosyalarını değiştirmez.
- Odak test başarısızsa tam teste geçmez.
- Tam test başarısızsa hata node kimliklerini ve pytest çıktısını rapora/loga yazar.
- Başarısızlıkta yanlış biçimde "kurulum geri alındı" demez; değişiklik yapılmadığını açıkça bildirir.
- Research Engine yalnızca tam test başarılıysa COMPLETE olarak işaretlenir.
