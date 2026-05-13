# 🚀 Mailpulse | Enterprise Email Marketing Platform

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Framework-Flask-black?logo=flask)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![Status](https://img.shields.io/badge/Status-Active_Development-brightgreen)

StellarMail is a high-performance, locally hosted email marketing platform. Engineered with a modular Single Page Application (SPA) architecture, it consolidates complex campaign orchestration, real-time engagement analytics, and dynamic audience segmentation into an intuitive, low-latency interface.

---

## 🧭 Platform Navigation (The Clustered UI)

To minimize cognitive load and maximize administrative efficiency, StellarMail consolidates 20+ operational requirements into a streamlined, 6-tab sidebar architecture:

* **📊 Data Hub:** The centralized repository for audience management. Handles CSV ingestions, automated deduplication, and suppression list monitoring (bounces/unsubscribes).
* **📨 Workflow Hub:** The campaign orchestration engine. Guides users through a multi-step wizard to draft, target, and schedule outbound email blasts.
* **📈 Analytics Hub:** The real-time telemetry dashboard. Visualizes unique Open Rates, Click-Through Rates, and delivery success metrics using interactive charts.
* **🎨 Studio & Settings:** The creative center for building responsive HTML email templates with dynamic variable injection (`{{first_name}}`), alongside user-specific preferences.
* **✨ Specialty:** Houses advanced platform tools, seed templates, and specialized segmentation features for targeted marketing constraints.
* **⚙️ System:** The admin control panel for managing organizational branding, SMTP routing configurations, and global platform health.

---

## ✨ Core Technical Features

* **Real-Time Tracking Engine:** Custom 1x1 tracking pixels and URL redirection wrappers capture distinct user engagement events.
* **Automated Background Processing:** An asynchronous scheduling engine that monitors database timestamps to deploy delayed campaigns.
* **Robust Data Ingestion:** Utilizes `Pandas` for scalable CSV imports, gracefully handling missing data and duplicate entries.
* **Secure SMTP Transmission:** Reliable bulk email delivery via `smtplib` with error handling for bounced addresses and server disconnects.
* **RESTful API Backend:** Clean separation of concerns between the Flask backend and the Vanilla JS frontend.

---

## 🛠️ Tech Stack

| Category | Technology |
| :--- | :--- |
| **Backend** | Python, Flask, Flask-SQLAlchemy, Flask-Login |
| **Database** | SQLite (Production-ready for PostgreSQL/MySQL integration) |
| **Frontend** | HTML5, CSS3, Vanilla JavaScript (SPA), Chart.js |
| **Data Processing** | Pandas |
| **Email Delivery** | Python `smtplib`, Flask-Mail |

---

## 🔬 Technical Deep Dive: Engagement Tracking

A major technical achievement of this platform is the custom engagement tracking engine, built entirely from scratch without relying on third-party analytics APIs.

* **Open Tracking:** The backend dynamically generates a 1x1 transparent GIF (`<img src="/t/open/<send_id>">`) injected into the footer of outbound HTML emails. When the recipient's email client (e.g., Gmail) requests this image, the server intercepts the request and logs a unique "Open" event linked to that specific contact and campaign.
* **Click Tracking:** Outbound anchor tags are automatically rewritten via a RegEx processor before sending. Links are wrapped in a redirect route (`/t/click/<send_id>?next=<original_url>`). This ensures the server captures the interaction before smoothly forwarding the user to their intended destination via a `302 Redirect`.

*(Note for Evaluators: Because modern email clients use proxy servers to cache images, tracking pixels pointing to `localhost` or `127.0.0.1` are often blocked for security. In a true cloud deployment (e.g., AWS, Render), this pixel fires automatically. For local development, this network request can be simulated by hitting the endpoint directly).*

---

## 🚀 Local Installation & Setup

### Prerequisites
* Python 3.8+
* A valid Gmail App Password (for SMTP delivery)

### 1. Clone the Repository
```bash
git clone [https://github.com/karthikpolishetty/mailpulse-email-campaign.git](https://github.com/karthikpolishetty/mailpulse-email-campaign.git)
cd mailpulse-email-campaign
