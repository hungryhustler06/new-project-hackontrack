# SupplyChain Sentinel 🛡️

> Cryptographic Integrity & Deterministic Risk Surveillance for Modern Logistics

Built for **Hack On Track — Domain 5: Open Innovation (Cybersecurity / Software Track)**.

---

## 📌 Overview
SupplyChain Sentinel is a lightweight, tamper-evident logistics intelligence platform. It binds multi-checkpoint shipment lifecycles to **SHA-256 cryptographic fingerprints** to instantly catch silent data manipulation, while running real-time deterministic risk surveillance across shipments and warehouse nodes.

---

## 🚀 Features
- **Cryptographic Hash Verification (SHA-256):** Fingerprints critical shipment fields. Any unauthorized modification flags `TAMPER DETECTED`.
- **Interactive Tamper Demo:** One-click simulation of cargo data tampering and instant cryptographic restoration.
- **7 Deterministic Risk Rules:** Automated alerts for overdue delivery, skipped sequence checkpoints, customs holds, and warehouse capacity exceeding 90%.
- **Live Logistics Flow Pipeline:** Visual end-to-end corridor tracing from manufacturer to retailer.
- **Dynamic Report & CSV Exporters:** Real-time data aggregation with sanitized CSV downloads.
- **Interactive REST API Documentation:** Auto-generated interactive OpenAPI/Swagger docs via FastAPI.

---

## 🛠️ Tech Stack
- **Backend:** Python 3.13, FastAPI, Uvicorn
- **Security:** SHA-256 (Python `hashlib` & browser `Web Crypto API`)
- **Frontend:** Semantic HTML5, CSS3, Vanilla JavaScript, Chart.js
- **Data & Caching:** In-memory structured datasets with `localStorage` persistence

---

## ⚙️ Quick Start

### 1. Prerequisites
- Python 3.10+ installed

### 2. Install Dependencies
```bash
py -m pip install -r requirements.txt
```

### 3. Run the Application
```bash
py main.py
```

### 4. Access the Dashboard
- **Web Dashboard:** [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
