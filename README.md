# Capstone Project Module 1 – Hospital Patient Management

## 📌 Deskripsi
Project ini dibuat sebagai bagian dari **Capstone Project Module 1**.  
Aplikasi ini berbasis **Python** yang terhubung dengan **MySQL Database**, dengan 4 fitur utama:

1. **Read Table** – Menampilkan data pasien dari tabel `patients`.
2. **Show Statistik (Umur)** – Menampilkan statistik deskriptif dari kolom `age`.
3. **Data Visualization** – Menampilkan grafik pie chart proporsi gender dan histogram distribusi umur.
4. **Add Data** – Menambahkan data pasien baru ke tabel MySQL lewat terminal.

---

## 🗄️ Database
Database yang digunakan adalah **`hospital_db`** dengan tabel `patients`.  
Kolom yang tersedia:
- `patient_id` (INT, PK, AUTO_INCREMENT)
- `name` (VARCHAR)
- `gender` (ENUM: Male/Female)
- `age` (INT)
- `diagnosis` (VARCHAR)
- `admission_date` (DATE)
- `discharge_date` (DATE)
- `patient_type` (ENUM: Rawat Jalan, Rawat Inap, IGD)
- `department` (VARCHAR)
- `payment_method` (ENUM: BPJS, Asuransi, Umum)

Data dummy sudah disiapkan sebanyak **30 pasien**.

---