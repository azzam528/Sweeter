# Handoff Report — Sweeter New Home Feed Activation (Session 1)

## 1. Ringkasan Perubahan
Pada Session 1, template desain modern dari Google Stitch (`post.html`) berhasil diunduh dan diaktifkan sebagai halaman Home Feed utama pada aplikasi Sweeter Flask, menggantikan `index.html`. Halaman lama tetap dipertahankan pada route `/old` sebagai cadangan (fallback). Seluruh fungsionalitas backend dan database MongoDB (posting, reactions like/star/thumbs, comment & reply system bertingkat, soft delete, comment counter dinamis, dan XSS protection) terintegrasi secara mulus dengan desain Stitch berbasis Tailwind CSS dan Manrope typography.

---

## 2. Daftar File yang Dimodifikasi & Ditambahkan
1. `templates/post.html`: File template Home Feed baru yang telah disesuaikan dengan Jinja2 templating, integrasi `myjs.js`, Font Awesome 4.7, responsive container, post composer card, dan sticky stream header.
2. `templates/user_profile.html`: File template User Profile baru yang diunduh dari Stitch untuk persiapan Session 2.
3. `app.py`:
   - Route `/` dialihkan merender `post.html`.
   - Route cadangan `/old` ditambahkan untuk tetap melayani `index.html`.
   - Route `/user_old/<username>` ditambahkan sebagai cadangan untuk `user.html`.
   - Endpoint `/get_posts` diperbarui agar mendukung query parameter `username_give` untuk filter postingan per user.
4. `static/myjs.js`:
   - `get_posts()` diperbarui untuk mendeteksi class `bg-background` (Stitch template) dan merender kartu postingan bergaya modern Stitch dengan Tailwind CSS dan Material Symbols/Font Awesome icons, sambil tetap mendukung Bulma card jika dibuka dari template lama.
   - `toggle_like()`, `toggle_stars()`, `toggle_thumbs()` diperbarui untuk memberikan highlight status visual aktif (`text-primary font-bold`).
   - `render_comment_nodes()` dan `open_reply_form()` diperbarui untuk menampilkan node komentar dan form balasan bergaya Stitch pada template modern.
5. `static/mystyle.css`:
   - Scoped style lama `* { font-family: 'Noto Serif Display', serif; }` dan `body { background-color: #d4adad; }` ke `body:not(.bg-background)` agar tidak merusak tema dan font Manrope pada desain Stitch.
   - Menambahkan utility `.is-hidden { display: none !important; }`.

---

## 3. Daftar Route yang Diubah & Ditambahkan
| Route | Method | Template / Handler | Keterangan |
|---|---|---|---|
| `/` | GET | `post.html` | Halaman Home Feed utama baru (Stitch design) |
| `/old` | GET | `index.html` | Route cadangan / backup template lama |
| `/user/<username>` | GET | `user_profile.html` | Route profil pengguna baru |
| `/user_old/<username>` | GET | `user.html` | Route cadangan / backup profil lama |
| `/get_posts` | GET | `get_posts()` | Mendukung filter `username_give` opsional |

---

## 4. Daftar API yang Digunakan
- `GET /` — Memvalidasi JWT `mytoken`, mengambil `user_info`, merender `post.html`.
- `GET /get_posts` — Mengambil 20 postingan terbaru terurut kronologis terbalik beserta data reaksi dan `count_comments`.
- `POST /posting` — Membuat postingan baru dengan `comment_give` dan `date_give`.
- `POST /update_like` — Menambah/menghapus reaksi `heart`, `stars`, dan `thumbs`.
- `GET /get_comments` — Mengambil seluruh komentar untuk `post_id_give`.
- `POST /create_comment` — Mengirim komentar utama atau balasan (nested reply).
- `POST /delete_comment` — Menghapus komentar milik sendiri dengan soft delete.
- `POST /sign_out` — Menghapus cookie autentikasi `mytoken` dan mengarahkan ke `/login`.

---

## 5. Komponen UI yang Berhasil Diintegrasikan
- **Header / Top Navbar**: Brand "Sweeter", navigasi timeline, avatar dan nama pengguna dinamis dari database, tombol Sign Out fungsional.
- **Left Sidebar**: Navigasi Home (`/`), Profile (`/user/<username>`), tombol "Create Post" dengan auto-scroll dan focus ke composer, user profile badge di bagian bawah. Responsive: tersembunyi rapi di mobile dan tampil di tablet/desktop.
- **Post Composer**: Textarea "What's on your mind?", avatar pengguna dinamis, tombol "Post" via AJAX tanpa reload halaman, keyboard shortcut Enter untuk posting.
- **Feed Stream**: Post cards bergaya Stitch dengan nama pengguna, handle `@username`, waktu relatif (`time2str`), teks postingan aman XSS, toolbar reaksi (Heart, Star, Thumbs) dan Comment toggle.
- **Comment & Reply Thread**: Form komentar, collapsible thread, indentasi visual bertingkat (nested replies), avatar komentator, tombol Reply dan Delete (khusus komentar sendiri).
- **Right Sidebar**: Trending topics & footer.

---

## 6. Fitur yang Berhasil Diuji
1. Login dan autentikasi token JWT.
2. Membuka Home Feed (`/`) dengan `post.html`.
3. Membuka Home Feed cadangan (`/old`) dengan `index.html`.
4. Menampilkan daftar postingan dari database MongoDB.
5. Membuat postingan baru via composer tanpa reload halaman.
6. Reaksi Like (Heart), Star, dan Thumbs beserta counter yang terbarui otomatis.
7. Membuka dan menutup collapsible comment section.
8. Menambahkan komentar utama pada postingan.
9. Menambahkan balasan komentar (nested reply).
10. Menghapus komentar sendiri (soft-delete).
11. Proteksi otorisasi penghapusan komentar orang lain (HTTP 403).
12. Sign out server-side via `/sign_out`.
13. Proteksi akses halaman tanpa token (redirect ke `/login`).

---

## 7. Hasil Testing PASS/FAIL
- Automated Backend Regression Tests (`run_backend_tests.py`): **14/14 PASS (100%)**
- Template Rendering & Jinja2 Syntax Check: **PASS**
- Responsive Layout (Mobile, Tablet, Desktop): **PASS**

---

## 8. Bug yang Ditemukan & Diperbaiki
- **Bug global CSS font override**: Selector `* { font-family: 'Noto Serif Display'; }` di `mystyle.css` menimpa font `Manrope` di template Stitch. *Solusi*: Dibatasi hanya untuk `body:not(.bg-background)`.
- **Bug missing `.is-hidden`**: Di Bulma `.is-hidden` ada di `bulma.min.css`, namun pada template Tailwind `.is-hidden` tidak ada sehingga bagian komentar tidak dapat ditutup. *Solusi*: Ditambahkan `.is-hidden { display: none !important; }` di `mystyle.css`.
- **Bug `username_give` di `/get_posts`**: `/get_posts` di `app.py` awalnya mengabaikan query `username_give` dan selalu memanggil `db.posts.find({})`. *Solusi*: Ditambahkan pengecekan `query = {'username': username_receive} if username_receive else {}`.

---

## 9. TODO / Catatan untuk Session 2
- Integrasi data aktual pada halaman User Profile (`user_profile.html`):
  - Mengganti dummy stats (842 following, 12.4k followers, +28% reader status) dengan data aktual / jumlah postingan riil.
  - Memastikan jika user tidak ditemukan di `/user/<username>`, menghasilkan respon 404 yang tepat bukan render crash.
  - Memastikan tab navigasi dan bio terhubung dengan profil yang dibuka.
  - Fitur follow/following belum didukung oleh schema database `users`; jangan buat tombol follow palsu, catat sebagai TODO.
- Menghubungkan klik avatar di Home Feed ke `/user/<username>`.

---

## 10. Status Akhir
Session 1 selesai dengan sukses. Semua fungsionalitas dan aturan kerja terpenuhi.
