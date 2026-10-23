import mysql.connector
import pandas as pd
import matplotlib.pyplot as plt


# Connect Database
def connect_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",        
        password="admin",
        database="hospital_db"
    )


# Fitur 1: Read Table
def read_table():
    conn = connect_db()
    df = pd.read_sql("SELECT * FROM patients", conn)
    conn.close()
    print("\n### DATA PASIEN ###")
    print(df.to_string(index=False))

# Fitur 2: Show Statistik (Age, dan lengkap)
def show_statistics_age():
    df = pd.read_sql("SELECT age FROM patients", connect_db())
    s = df["age"].dropna()
    
    print("\n### STATISTIK UMUR PASIEN (age) ###")
    print(f"Count: {s.count()}")
    print(f"Mean: {s.mean():.2f}")
    print(f"Median: {s.median():.2f}")
    print(f"Std: {s.std(ddof=1):.2f}")
    print(f"Min: {s.min()}")
    print(f"Max: {s.max()}")

# Fitur 3: Data Visualization
def visualize():
    conn = connect_db()
    df = pd.read_sql("SELECT gender, age, patient_type FROM patients", conn)
    conn.close()

    # Pie chart distribusi gender
    df["gender"].value_counts().plot(kind="pie", autopct="%1.1f%%", title="Distribusi Gender")
    plt.ylabel("")
    plt.show()

    # Histogram umur pasien
    df["age"].plot(kind="hist", bins=10, title="Distribusi Umur")
    plt.xlabel("Umur")
    plt.show()

# Fitur 4: Add Data
def add_data():
    print("\n### Tambah Data Pasien ###")
    name = input("Nama: ")
    gender = input("Gender (Male/Female): ")
    age = int(input("Umur: "))
    diagnosis = input("Diagnosis: ")
    admission_date = input("Tanggal Masuk (YYYY-MM-DD): ")
    discharge_date = input("Tanggal Keluar (YYYY-MM-DD, boleh kosong): ")
    patient_type = input("Patient Type (Rawat Jalan/Rawat Inap/IGD): ")
    department = input("Department: ")
    payment_method = input("Payment Method (BPJS/Asuransi/Umum): ")

    conn = connect_db()
    cur = conn.cursor()
    sql = """
        INSERT INTO patients
        (name, gender, age, diagnosis, admission_date, discharge_date, patient_type, department, payment_method)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    cur.execute(sql, (name, gender, age, diagnosis, admission_date or None,
                      discharge_date or None, patient_type, department, payment_method))
    conn.commit()
    cur.close(); conn.close()
    print("[OK] Data pasien berhasil ditambahkan.")

# Menu Utama
def main():
    while True:
        print("\n### MENU UTAMA ###")
        print("1. Tampilkan Data Pasien")
        print("2. Show Statistik (Umur)")
        print("3. Data Visualization")
        print("4. Tambah Data Pasien")
        print("5. Keluar")

        pilihan = input("Pilih menu (1-5): ")

        if pilihan == "1":
            read_table()
        elif pilihan == "2":
            show_statistics_age()
        elif pilihan == "3":
            visualize()
        elif pilihan == "4":
            add_data()
        elif pilihan == "5":
            print("Keluar dari program.")
            break
        else:
            print("[ERR] Pilihan tidak valid.")

if __name__ == "__main__":
    main()