# Servis Rota Optimizasyonu – Esnek Doluluk

Bu sürüm Streamlit Community Cloud üzerinde çalışmak üzere hazırlanmıştır.

## Temel değişiklik

Araçlar artık eşit yolcu sayısına zorlanmaz. Araç kapasitesi yalnızca maksimum sınırdır.
Örneğin 4 rota için 40 / 36 / 25 / 18 gibi farklı doluluklar oluşabilir. Böylece operasyon
tarafında yoğun rotalara büyük, daha düşük doluluklu rotalara küçük servis atanabilir.

## Planlama kuralları

- Araç kapasitesi üst sınır olarak uygulanır.
- Sabit 4 rota seçildiğinde 4 aktif rota korunur.
- Yolcu sayıları rotalar arasında eşitlenmez.
- Mevcut durağa eklemede en kısa yürüme mesafesi önceliklidir.
- Yeni durak gerekiyorsa mevcut durağın tekrar kullanılması ve en düşük ek rota süresi önceliklidir.
- Azami rota süresi, yürüme mesafesi ve durak bekleme süresi uygulanmaya devam eder.
- Sabah hedef fabrika varışı 07:55, akşam fabrika çıkışı 17:40'tır.
- Durak başına bekleme süresi 15 saniyedir.

## Streamlit Cloud

Repository: `ceydaariisoy/servis-optimizasyonu-esnek-doluluk`

Main file path: `app.py`

Bağımlılıklar `requirements.txt` üzerinden otomatik kurulur.
