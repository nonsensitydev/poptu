# POPCAT เตรียมอุดมศึกษา (Self-Hosted)

เว็บเกมคลิกสะสมคะแนนประจำตึกเรียนทั้งหมด 11 ตึกของโรงเรียนเตรียมอุดมศึกษา พร้อมระบบซิงก์คะแนนแบบเรียลไทม์ (Server-Sent Events) และเก็บข้อมูลถาวรด้วย SQLite (WAL Mode)

## รายชื่อตึกเรียนทั้งหมด (11 ตึก)
1. ตึก 1
2. ตึก 2
3. ตึก 3
4. ตึก 4
5. ตึก 8
6. ตึก 9
7. ตึก 50 ปี
8. ตึก 55 ปี
9. ตึก 60 ปี
10. ตึกคุณหญิงหรั่ง กันตารัติ
11. ตึกศิลปะ

---

## วิธีที่ 1: รันด้วย Docker Compose (แนะนำ)

1. แตกไฟล์ ZIP และเข้าไปในโฟลเดอร์โปรเจกต์:
   ```bash
   unzip popcat-triam-udom.zip
   cd popcat-triam-udom
   ```

2. สร้างและรันคอนเทนเนอร์:
   ```bash
   docker compose up -d --build
   ```
   - เปิดใช้งานที่ `http://localhost:8080` (เปลี่ยนพอร์ตได้ผ่าน `HOST_PORT=8085 docker compose up -d`)
   - ฐานข้อมูล SQLite จะถูกเก็บถาวรไว้ที่ `./data/popcat.db` บนเครื่องโฮสต์

---

## วิธีที่ 2: รันบน Linux โดยตรง (Python venv)

1. ติดตั้ง Dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. รันเซิร์ฟเวอร์:
   ```bash
   DB_PATH=./data/popcat.db PORT=8080 uvicorn server:app --host 0.0.0.0 --port 8080
   ```

---

## การตั้งค่าหลัง Reverse Proxy (Nginx / Nginx Proxy Manager / Caddy)

แอปพลิเคชันใช้ **Server-Sent Events (`/api/stream`)** สำหรับอัปเดตคะแนนข้ามทุกอุปกรณ์แบบเรียลไทม์ และส่ง Header `X-Accel-Buffering: no` ไว้แล้ว หากตั้งค่า Nginx Custom Location เพิ่มเติม แนะนำให้ปิด Proxy Buffering:

```nginx
location /api/stream {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
}
```
