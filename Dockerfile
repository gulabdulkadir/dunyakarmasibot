# Resmi Python imajını kullan
FROM python:3.10-slim

# Çalışma dizinini ayarla
WORKDIR /app

# Gereksinim dosyasını kopyala ve kütüphaneleri yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot kodunu klasöre kopyala
COPY . .

# Botu çalıştır (dosya adın ronaldobot.py olduğu için bu şekilde kalacak)
CMD ["python", "ronaldobot.py"]