CREATE DATABASE IF NOT EXISTS hospital_db;
USE hospital_db;

DROP TABLE IF EXISTS patients;
CREATE TABLE patients (
		patient_id INT PRIMARY KEY AUTO_INCREMENT,
        name VARCHAR(100) NOT NULL,
        gender ENUM('Male','Female') NOT NULL,
        age INT NOT NULL,
        diagnosis VARCHAR(300),
        admission_date DATE,
        discharge_date DATE,
        patient_type ENUM('Rawat Jalan','Rawat Inap','IGD') NOT NULL,
        department VARCHAR(100),
        payment_method ENUM('BPJS','Asuransi','Umum') NOT NULL
);

INSERT INTO patients
(name, gender, age, diagnosis, admission_date, discharge_date, patient_type, department, payment_method) VALUES
('Andi Setiawan','Male',45,'Diabetes','2025-08-01','2025-08-05','Rawat Inap','Internal Medicine','BPJS'),
('Budi Santoso','Male',32,'Hipertensi','2025-08-10','2025-08-14','Rawat Jalan','Internal Medicine','Umum'),
('Citra Dewi','Female',29,'Demam Berdarah','2025-08-15','2025-08-21','Rawat Inap','Anak','BPJS'),
('Dewi Lestari','Female',50,'Diabetes','2025-08-20','2025-08-27','Rawat Jalan','Internal Medicine','Asuransi'),
('Eka Pratama','Male',38,'Asma','2025-08-22','2025-08-25','Rawat Jalan','Paru','BPJS'),
('Fitri Rahmawati','Female',26,'Pneumonia','2025-08-05','2025-08-12','Rawat Inap','Paru','Umum'),
('Gilang Nugroho','Male',40,'COVID-19','2025-07-28','2025-08-04','Rawat Inap','Paru','BPJS'),
('Hana Sari','Female',35,'Gastritis','2025-07-30','2025-08-03','Rawat Jalan','Internal Medicine','Umum'),
('Indra Wijaya','Male',60,'Hipertensi','2025-07-15','2025-07-22','Rawat Inap','Internal Medicine','BPJS'),
('Julia Anggraini','Female',42,'Migren','2025-06-20','2025-06-24','Rawat Jalan','Neurologi','Asuransi'),
('Krisna Mahendra','Male',55,'Diabetes','2025-06-15','2025-06-25','Rawat Jalan','Internal Medicine','BPJS'),
('Linda Wulan','Female',47,'Asma','2025-07-10','2025-07-14','Rawat Jalan','Paru','Umum'),
('Made Gunawan','Male',65,'Stroke','2025-05-05','2025-05-18','Rawat Inap','Neurologi','Asuransi'),
('Nadia Putri','Female',23,'Demam Berdarah','2025-08-08','2025-08-15','Rawat Inap','Anak','BPJS'),
('Oka Permata','Male',36,'Pneumonia','2025-08-12','2025-08-18','Rawat Jalan','Paru','BPJS'),
('Putri Utami','Female',31,'Gastritis','2025-06-02','2025-06-09','Rawat Jalan','Internal Medicine','Umum'),
('Rizki Saputra','Male',27,'Diare Akut','2025-07-03','2025-07-07','IGD','Internal Medicine','Umum'),
('Sari Indah','Female',58,'Hipertensi','2025-05-28','2025-06-10','Rawat Inap','Internal Medicine','BPJS'),
('Taufik Hidayat','Male',44,'Tuberkulosis','2025-04-25','2025-05-05','Rawat Inap','Paru','BPJS'),
('Uli Anggun','Female',39,'COVID-19','2025-04-12','2025-04-20','Rawat Inap','Paru','Asuransi'),
('Valen Prasetyo','Male',30,'Demam Berdarah','2025-08-03','2025-08-09','Rawat Jalan','Anak','BPJS'),
('Wulan Kartika','Female',55,'Hipertensi','2025-03-15','2025-03-30','Rawat Inap','Internal Medicine','Asuransi'),
('Yuda Kurniawan','Male',48,'Diabetes','2025-07-01','2025-07-08','Rawat Jalan','Internal Medicine','BPJS'),
('Zahra Permata','Female',20,'Diare Akut','2025-08-18','2025-08-24','IGD','Internal Medicine','Umum'),
('Arif Susanto','Male',72,'Stroke','2025-02-01','2025-02-12','Rawat Inap','Neurologi','BPJS'),
('Bella Anggraini','Female',63,'Hipertensi','2025-01-20','2025-01-27','Rawat Jalan','Internal Medicine','BPJS'),
('Chandra Gunawan','Male',28,'Asma','2025-03-07','2025-03-12','Rawat Jalan','Paru','Asuransi'),
('Dian Maharani','Female',34,'Migren','2025-02-25','2025-03-02','Rawat Jalan','Neurologi','Umum'),
('Edi Syahputra','Male',52,'Pneumonia','2025-05-14','2025-05-25','Rawat Inap','Paru','BPJS'),
('Farah Lestari','Female',41,'COVID-19','2025-06-17','2025-06-24','Rawat Inap','Paru','Asuransi');

USE hospital_db;

SHOW TABLES;
SELECT * FROM patients LIMIT 100;
        