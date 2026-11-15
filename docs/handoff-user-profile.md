# Handoff Report — Sweeter User Profile Activation (Session 2)

## 1. Ringkasan Perubahan
Pada Session 2, halaman User Profile baru dari Google Stitch (`user_profile.html`) berhasil diaktifkan secara penuh dan diintegrasikan dengan backend Flask dan database MongoDB. Halaman profil sekarang:
- Dinamis sesuai parameter URL username (`/user/<username>`).
- Mengambil dan menampilkan data aktual profil dari MongoDB (nama, username, bio, avatar, serta counter postingan riil).
- Menghapus seluruh placeholder dan angka dummy Stitch (dummy following, dummy followers, dummy activity rating).
- Menampilkan daftar postingan yang hanya dimiliki oleh pengguna profil tersebut (isolasi postingan per user).
- Membedakan antara profil sendiri (`status = True`, tombol "Edit Profile" aktif beserta form AJAX update profil) dan profil pengguna lain (`status = False`, tombol "Edit Profile" disembunyikan dan tombol "Follow" dinonaktifkan dengan status *Coming Soon* karena fitur follow belum ada di skema database).
- Menampilkan halaman 404 modern jika username tidak ditemukan di database.
- Menjaga integritas interaksi Feed, Reaction (Like, Star, Thumbs), Comment System, Nested Replies, dan Delete Comment pada postingan di halaman profil.

---

## 2. File yang Diubah & Ditambahkan
1. `templates/user_profile.html`:
   - Disesuaikan dengan Jinja2 template variables (`user_info`, `current_user`, `status`, `post_count`).
   - Header dan Sidebar terhubung dengan akun user yang sedang login (`current_user`), sedangkan Profile Header terhubung dengan profil yang sedang dilihat (`user_info`).
   - Menghubungkan form Edit Profile (nama, bio, foto profil) via AJAX ke endpoint `/update_profile`.
   - Menambahkan tombol Share Profile fungsional (copy link profil ke clipboard dengan feedback visual).
   - Menambahkan responsivitas mobile (`pl-0 md:pl-[264px]`, `hidden md:flex`).
2. `templates/404.html`:
   - Template halaman 404 modern baru bergaya Stitch warm minimalism jika username tidak ditemukan.
3. `app.py`:
   - Route `/user/<username>` diperbarui: memvalidasi token JWT, mengambil data `current_user` dan `user_info`, mengembalikan HTTP 404 jika user tidak ditemukan, menghitung `post_count`, dan meneruskan `status`.
   - Route `/user_old/<username>` diperbarui untuk memeriksa keberadaan user dan mengembalikan 404 jika tidak ditemukan.
   - Route `/get_posts` diperbarui: membersihkan parameter `username_give.strip()` untuk query filter postingan.
4. `static/myjs.js`:
   - Menambahkan visual empty-state pada `get_posts()` jika pengguna belum memiliki postingan (baik tampilan Stitch modern maupun Bulma legacy).
5. `test_profile_system.py`:
   - Script pengujian komprehensif untuk memvalidasi seluruh 10 skenario pengetesan profil.

---

## 3. Route yang Ditambahkan / Diubah
| Route | Method | Status | Keterangan |
|---|---|---|---|
| `/user/<username>` | GET | Diubah | Halaman User Profile baru (`user_profile.html`). Mengembalikan 404 jika user tidak ditemukan |
| `/user_old/<username>` | GET | Diubah | Route backup ke `user.html` lama. Mengembalikan 404 jika user tidak ditemukan |
| `/get_posts` | GET | Dioptimasi | Mendukung filter per user secara presisi via parameter `username_give` |

---

## 4. API yang Digunakan
- `GET /user/<username>` — Menampilkan halaman profil user.
- `GET /get_posts?username_give=<username>` — Mengambil postingan milik user tersebut.
- `POST /update_profile` — Memperbarui nama profil, deskripsi bio, dan file gambar avatar.
- `POST /update_like` — Memberikan reaksi like/star/thumbs pada postingan di profil.
- `GET /get_comments?post_id_give=<post_id>` — Mengambil komentar pada postingan di profil.
- `POST /create_comment` — Mengirim komentar utama atau balasan (nested reply).
- `POST /delete_comment` — Menghapus komentar sendiri (soft delete).
- `POST /sign_out` — Logout dan hapus cookie autentikasi `mytoken`.

---

## 5. Integrasi Data Profil
- **Profile Picture**: Menampilkan foto asli dari `user_info.profile_pic_real` dengan proteksi sanitasi URL dan fallback ke `profile_placeholder.png`.
- **Display Name**: Menampilkan `user_info.profile_name` yang di-escape XSS.
- **Username**: Menampilkan handle `@{{ user_info.username }}`.
- **Bio**: Menampilkan `user_info.profile_info`.
- **Jumlah Postingan**: Menampilkan angka riil hasil agregasi `db.posts.count_documents({'username': username})`.
- **Followers & Following**: Fitur follow belum ada pada skema dokumen `db.users`. Angka dummy (842 / 12.4k) telah dihapus dan diganti dengan status informatif *Followers & Following (Coming Soon)* tanpa membuat fungsi follow palsu.
- **Daftar Postingan**: Menampilkan postingan milik pengguna terkait secara dinamis via AJAX, dengan penanganan empty-state jika belum ada kiriman.

---

## 6. Hasil Testing
Pengujian dilakukan menggunakan script otomatis `test_profile_system.py`:

| No | Skenario Uji | Status | Keterangan |
|---|---|---|---|
| 1 | Membuka profil sendiri | **PASS** | Status 200, tombol "Edit Profile" tampil dan fungsional |
| 2 | Membuka profil pengguna lain | **PASS** | Status 200, tombol "Edit Profile" tidak tampil, tombol Follow disabled |
| 3 | Profil dengan banyak postingan | **PASS** | Mengambil seluruh postingan milik user terkait |
| 4 | Profil tanpa postingan | **PASS** | Mengembalikan 0 posts dengan tampilan empty state informatif |
| 5 | Username tidak ditemukan | **PASS** | Status HTTP 404 dan merender halaman `404.html` |
| 6 | Postingan hanya milik pengguna terkait | **PASS** | Terisolasi 100%, tidak ada postingan user lain yang bocor |
| 7 | Navigasi dari feed ke profil | **PASS** | Link avatar dan username di feed mengarah ke `/user/<username>` |
| 8 | Navigasi dari profil kembali ke feed | **PASS** | Link Home dan logo mengarah ke `/` |
| 9 | Komentar dan reaksi pada postingan profil | **PASS** | Reaksi, pembuatan komentar, nested reply, dan delete berjalan lancar |
| 10 | Tampilan mobile & responsivitas | **PASS** | Layout responsif tanpa horizontal overflow |

Hasil pengujian regresi backend menyeluruh (`run_backend_tests.py`): **14/14 PASS (100%)**.

---

## 7. Bug yang Ditemukan & TODO

### Bug yang Diperbaiki:
1. **User Not Found Crash / 200 Empty**: Sebelumnya bila username tidak ada, Flask tetap merender template dengan `user_info=None` status 200. Sekarang divalidasi dan mengembalikan status 404 dengan template `404.html`.
2. **Postingan Profil Menampilkan Semua Postingan**: Sebelumnya `/get_posts` mengabaikan `username_give`. Sekarang difilter presisi di MongoDB query.
3. **Sidebar / Header Menampilkan Data Target User**: Pada template awal, jika User A membuka profil User B, header menampilkan nama User B. Sekarang header dan sidebar menampilkan `current_user` (user yang sedang login).
4. **Empty Post Feed Blank**: Feed pengguna tanpa postingan sebelumnya kosong melompong. Sekarang menampilkan empty state informatif.

### TODO untuk Masa Depan:
1. **Sistem Follow/Unfollow**: Membutuhkan penambahan koleksi `db.follows` atau field `followers`/`following` pada skema database.
2. **Tab Profile Tambahan**: Tab *Replies*, *Media*, dan *Likes* saat ini diberi label Coming Soon.
3. **Custom Banner / Cover Image**: Skema database saat ini belum menyimpan field cover image kustom per pengguna.

---

## 8. Catatan untuk Session 3
- Seluruh fondasi Home Feed (`post.html`) dan User Profile (`user_profile.html`) telah selesai diaktifkan dan diuji.
- Sistem autentikasi, routing, dan database MongoDB berjalan stabil.
- Sesi 2 selesai dengan sukses.
