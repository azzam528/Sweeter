# Sweeter — Security & Configuration Hardening Guide

Dokumen ini menjelaskan konfigurasi keamanan, pengelolaan environment variables, spesifikasi JWT, kebijakan cookie, perlindungan kredensial, dan panduan rotasi secret pada aplikasi **Sweeter**.

---

## 1. Pengelolaan Environment Variables

Aplikasi Sweeter menggunakan modul `python-dotenv` untuk memuat variabel lingkungan dari file `.env` pada root direktori proyek.

### 1.1 Variabel Lingkungan yang Didukung

| Variabel | Tipe / Format | Sifat | Deskripsi |
|---|---|---|---|
| `SECRET_KEY` | String (Minimal 32 byte / 64 hex char) | **Wajib** | Kunci rahasia untuk menandatangani dan memverifikasi token JWT (algoritma HMAC-SHA256). |
| `MONGODB_CONNECTION_STRING` | URI Connection String | **Wajib** | URI koneksi MongoDB Atlas atau lokal (contoh: `mongodb+srv://...`). |
| `MONGODB_DATABASE` | String | Opsional | Nama database MongoDB (default: `dbsparta_plus_week4`). |
| `APP_ENV` | `development` / `production` | Opsional | Mode lingkungan aplikasi (default: `development`). |

### 1.2 Perlindungan File `.env`
- File `.env` **wajib diabaikan** dari pelacakan version control Git.
- Aturan `.gitignore` telah dikonfigurasi:
  ```gitignore
  .env
  .env.*
  !.env.example
  ```
- File template konfigurasi aman disediakan melalui [`.env.example`](file:///.env.example) yang berisi placeholder tanpa memuat kredensial atau password asli.

---

## 2. SECRET_KEY & Persyaratan HS256

### 2.1 Standar RFC 7518 Section 3.2
Aplikasi Sweeter menggunakan algoritma `HS256` (HMAC menggunakan SHA-256) untuk JSON Web Token (JWT).
- Berdasarkan **RFC 7518 Section 3.2**: Kunci penandatanganan HMAC-SHA256 **wajib memiliki panjang minimal sama dengan panjang output hash**, yaitu **256 bit (32 byte)**.
- Kunci yang memiliki panjang kurang dari 32 byte rentan terhadap serangan kamus (*dictionary attack*) atau *brute-force offline* jika penyerang berhasil mendapatkan sampel token JWT.

### 2.2 Validasi Startup di Backend
Pada modul `app.py`, validasi otomatis dijalankan saat server diinisialisasi:
```python
SECRET_KEY_BYTES = SECRET_KEY.encode("utf-8")
if len(SECRET_KEY_BYTES) < 32:
    if IS_PRODUCTION:
        raise RuntimeError("CRITICAL: Pada environment production, SECRET_KEY wajib minimal 32 byte (256-bit).")
    else:
        warnings.warn("[SECURITY WARNING] Panjang SECRET_KEY kurang dari 32 byte...", UserWarning)
```
- **Mode Development:** Mengeluarkan peringatan terstruktur (*Security Warning*) tanpa menghentikan server lokal.
- **Mode Production:** Menolak menjalankan aplikasi (*Hard Stop*) demi mencegah celah keamanan di server live.

---

## 3. Panduan Pembuatan & Rotasi SECRET_KEY

### 3.1 Cara Membuat Secret Key Baru yang Aman
Gunakan pustaka kriptografi bawaan Python (`secrets`) untuk menghasilkan string acak berkekuatan 256-bit (64 karakter hexadecimal):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

*Contoh output yang aman:*
`e4a1b8c9f0d2e3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8` *(64 karakter hex = 32 byte)*.

### 3.2 Langkah-Langkah Rotasi Secret Key
Lakukan rotasi ketika:
1. Kunci rahasia diduga telah bocor atau tersimpan di log.
2. Ingin meningkatkan panjang kunci ke standar minimal 32 byte sebelum deployment production.
3. Pergantian berkala sesuai standar operasional keamanan.

#### Langkah Pelaksanaan:
1. **Pemberitahuan Sesi:** Rotasi kunci akan menyebabkan seluruh token JWT yang telah terbit menjadi tidak valid (*signature mismatch*). Seluruh pengguna yang sedang login akan otomatis dialihkan ke halaman `/login` pada request berikutnya.
2. **Generate Kunci Baru:** Jalankan perintah Python di atas untuk menghasilkan kunci 64-hex char baru.
3. **Perbarui `.env`:** Buka file `.env` di komputer lokal/server, perbarui nilai `SECRET_KEY`:
   ```dotenv
   SECRET_KEY=kunci_acak_baru_minimal_32_byte_hasil_generate_di_atas
   ```
4. **Restart Aplikasi:** Muat ulang aplikasi Flask.
5. **Verifikasi:** Lakukan pengujian login melalui browser untuk memastikan token baru diterbitkan dan diverifikasi tanpa peringatan `InsecureKeyLengthWarning`.

---

## 4. Kebijakan Cookie & Autentikasi

### 4.1 Parameter Cookie `mytoken`

| Atribut Cookie | Nilai Development | Nilai Production | Alasan Keamanan |
|---|---|---|---|
| **Path** | `/` | `/` | Cookie dapat diakses di seluruh rute aplikasi Sweeter. |
| **Max-Age** | 86.400 detik (24 jam) | 86.400 detik (24 jam) | Masa aktif token JWT dibatasi 24 jam. |
| **SameSite** | `Lax` | `Lax` | Melindungi dari serangan *Cross-Site Request Forgery* (CSRF) saat request berasal dari domain pihak ketiga. |
| **Secure** | `False` | `True` | Di development (HTTP localhost), cookie tetap bekerja. Di production, cookie wajib dikirim hanya melalui enkripsi HTTPS. |
| **HttpOnly** | `False` *(Hybrid)* | `False` *(Hybrid)* | Mempertahankan kompatibilitas dengan plugin client-side `$.cookie` dan fungsi logout `$.removeCookie('mytoken')` pada antarmuka saat ini. |

### 4.2 Endpoint Logout Server-Side
Untuk mendukung penghapusan cookie secara aman dari server, telah ditambahkan endpoint:
- **Rute:** `GET /sign_out` & `POST /sign_out`
- **Tindakan:** Mengarahkan ke `/login` dan mengirimkan header HTTP response `Set-Cookie: mytoken=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/`.

---

## 5. Ringkasan Autentikasi & Otorisasi

1. **Integritas Identitas Server-Side:**
   - Seluruh endpoint terlindungi (`/posting`, `/update_profile`, `/update_like`, `/create_comment`, `/get_comments`, `/delete_comment`) **selalu mengekstrak username dari token JWT terverifikasi** (`payload.get('id')`).
   - Parameter username dari form frontend tidak pernah dipercaya sebagai identitas pembuat aksi.
2. **Penanganan Error Token Terpusat:**
   - Semua rute menangkap `(jwt.ExpiredSignatureError, jwt.exceptions.PyJWTError)` secara menyeluruh.
   - Request API mengembalikan status **HTTP 401 Unauthorized** murni berbentuk JSON `{"result": "fail", "msg": "..."}`.
   - Request halaman browser mengarahkan pengguna ke `/login?msg=...`.
3. **Otorisasi Kepemilikan Resource:**
   - Endpoint penghapusan komentar (`POST /delete_comment`) memeriksa secara ketat `comment.username == payload.get('id')`.
   - Percobaan manipulasi dari pengguna lain ditolak dengan status **HTTP 403 Forbidden**.

---

## 6. Keterbatasan Keamanan yang Masih Ada & Rekomendasi Lanjutan

1. **Hashing Password Akun Pengguna:**
   - Saat ini tabel `users` menyimpan password menggunakan `hashlib.sha256(password.encode('utf-8')).hexdigest()` tanpa *salt*.
   - *Rekomendasi Lanjutan:* Di masa depan, lakukan migrasi bertahap ke algoritma hashing modern yang memiliki *work factor* dan *salt* otomatis (seperti `bcrypt` atau `Argon2` menggunakan pustaka `werkzeug.security.generate_password_hash`).
2. **Transisi Penuh ke HttpOnly Cookie:**
   - Untuk memproteksi token JWT dari akses JavaScript pihak ketiga secara total, disarankan mengonversi cookie `mytoken` menjadi `HttpOnly=True` murni, dan mengubah fungsi logout frontend di `templates/user.html` agar memanggil endpoint `POST /sign_out`.

