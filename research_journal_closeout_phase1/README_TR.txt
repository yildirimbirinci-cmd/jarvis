RESEARCH JOURNAL CLOSEOUT PHASE 1

Kaynak dogrusu: https://github.com/yildirimbirinci-cmd/jarvis
Research Engine'e dokunmaz. Yeni ve ayri bir closeout katmani ekler.

KURULUM
1. ZIP'i bir klasore cikarin.
2. PowerShell'de o klasore girin.
3. Jarvis sanal ortami acikken calistirin:
   python .\install_research_journal_closeout_phase1.py

Kurucu:
- Proje kokunu otomatik bulur.
- Var olan hedef dosyalar varsa .jarvis_fix_backup altina yedekler.
- Python derleme kontrolu yapar.
- Odak testini calistirir.
- Tam pytest paketini calistirir.
- Basarisizlikta degisiklikleri geri alir.
- Basarida proje ust klasorune research_journal_closeout_phase1_report.json yazar.

EKLENEN DOSYALAR
- core/research_journal_closeout.py
- tests/test_research_journal_closeout_phase1.py
