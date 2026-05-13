import os
import re
import uuid
import time
import base64
import smtplib
import html as html_lib
import pandas as pd
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlparse
from flask import Flask, request, jsonify, send_from_directory, Response, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from flask_cors import CORS
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text, or_, func
from sqlalchemy.orm import joinedload

app = Flask(__name__, static_folder=".")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
CORS(app)

# --- DATABASE & MAIL CONFIG ---
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///platform.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# GMAIL CONFIGURATION (preserve keys; password may be overridden by env)
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USE_SSL"] = False
app.config["MAIL_USERNAME"] = "Karthik.polishetty1432@gmail.com"
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "hvvd cjyt ohlz xobc")

# Public URLs for tracking links in outbound mail
app.config["PUBLIC_BASE_URL"] = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
db = SQLAlchemy(app)
mail = Mail(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


@login_manager.unauthorized_handler
def _unauthorized():
    return jsonify({"error": "Unauthorized"}), 401


# --- MODELS ---


class User(UserMixin, db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="Manager")  # Admin, Manager
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ContactList(db.Model):
    __tablename__ = "contact_list"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Contact(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(120), unique=True, nullable=False)
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    status = db.Column(db.String(20), default="active")  # active, unsubscribed, bounced
    source = db.Column(db.String(100), default="CSV Import")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    contact_list_id = db.Column(db.String(36), db.ForeignKey("contact_list.id"), nullable=True)
    contact_list = db.relationship("ContactList", backref=db.backref("contacts", lazy="dynamic"))


class Template(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False)
    html_body = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Campaign(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False, default="Campaign")
    subject = db.Column(db.String(500), nullable=False, default="")
    body_html = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft, scheduled, sending, sent
    scheduled_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    template_id = db.Column(db.String(36), db.ForeignKey("template.id"), nullable=True)
    contact_list_id = db.Column(db.String(36), db.ForeignKey("contact_list.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmailSend(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = db.Column(db.String(36), db.ForeignKey("campaign.id"), nullable=False)
    contact_id = db.Column(db.String(36), db.ForeignKey("contact.id"), nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)
    campaign = db.relationship("Campaign", backref=db.backref("email_sends", lazy="dynamic"))
    contact = db.relationship("Contact", backref=db.backref("email_sends", lazy="dynamic"))


class EmailEvent(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email_send_id = db.Column(db.String(36), db.ForeignKey("email_send.id"), nullable=False)
    event_type = db.Column(db.String(40), nullable=False)  # opened, clicked
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    email_send = db.relationship("EmailSend", backref=db.backref("events", lazy="dynamic"))


class OrganizationSettings(db.Model):
    """Single-row org branding / defaults (demo-friendly)."""
    __tablename__ = "organization_settings"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_name = db.Column(db.String(200), nullable=False, default="StellarMail")
    logo_url = db.Column(db.String(500), default="")
    default_from_name = db.Column(db.String(120), default="")
    default_from_email = db.Column(db.String(120), default="")
    aws_ses_region = db.Column(db.String(80), default="")
    aws_ses_note = db.Column(db.String(500), default="Configure in production via environment.")


PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def _ensure_sqlite_schema():
    """Add missing columns for existing SQLite DBs (create_all does not migrate)."""
    if "sqlite" not in app.config["SQLALCHEMY_DATABASE_URI"]:
        return
    with app.app_context():
        try:
            with db.engine.connect() as conn:
                rows = conn.execute(text("PRAGMA table_info(contact)")).fetchall()
                colnames = [r[1] for r in rows]
                if rows and "contact_list_id" not in colnames:
                    conn.execute(text("ALTER TABLE contact ADD COLUMN contact_list_id VARCHAR(36)"))
                    conn.commit()
                urows = conn.execute(text("PRAGMA table_info(user)")).fetchall()
                ucols = [r[1] for r in urows]
                if urows and "is_active" not in ucols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1"))
                    conn.commit()
        except Exception:
            pass


def _bootstrap_admin():
    if User.query.count() > 0:
        return
    email = os.environ.get("ADMIN_EMAIL", "admin@gmail.com")
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    u = User(email=email, role="Admin", is_active=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()


def _bootstrap_org_settings():
    if OrganizationSettings.query.first():
        return
    db.session.add(OrganizationSettings())
    db.session.commit()


def _is_safe_redirect_url(url):
    if not url or not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def rewrite_links_for_tracking(html, send_id):
    """Rewrite http(s) anchor hrefs through /t/click/<send_id>?next= (skips mailto, unsubscribe, tracking)."""
    if not html:
        return html
    base = app.config["PUBLIC_BASE_URL"]

    def sub(m):
        quote_ch = m.group(2)
        url = m.group(3).strip()
        low = url.lower()
        if low.startswith("#") or low.startswith("mailto:") or low.startswith("javascript:"):
            return m.group(0)
        if "/unsubscribe/" in low or "/t/open/" in low or "/t/click/" in low:
            return m.group(0)
        if not (low.startswith("http://") or low.startswith("https://")):
            return m.group(0)
        if not _is_safe_redirect_url(url):
            return m.group(0)
        tracked = f"{base}/t/click/{send_id}?next={quote(url, safe='')}"
        return f"href={quote_ch}{tracked}{quote_ch}"

    return re.sub(r"(?i)(href\s*=\s*)([\"'])([^\"']+)\2", sub, html)


def _footer_html(contact_id, send_id):
    base = app.config["PUBLIC_BASE_URL"]
    unsub_url = f"{base}/unsubscribe/{contact_id}"
    pixel_url = f"{base}/t/open/{send_id}"
    return (
        '<div style="margin-top:24px;padding-top:16px;border-top:1px solid #eee;font-size:12px;color:#666;">'
        f'<p><a href="{html_lib.escape(unsub_url)}">Unsubscribe</a></p>'
        f'<img src="{html_lib.escape(pixel_url)}" width="1" height="1" alt="" '
        'style="display:block;border:0;width:1px;height:1px;" />'
        "</div>"
    )


def _wrap_campaign_html(inner_html, contact, send_id):
    greeting = html_lib.escape(contact.first_name or "there")
    body = inner_html or ""
    footer = _footer_html(contact.id, send_id)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<p>Hi {greeting},</p>
{body}
{footer}
</body></html>"""


def _plain_from_html(html_str):
    # minimal strip for plaintext part
    t = html_str.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    if "<" in t:
        import re

        t = re.sub(r"<[^>]+>", "", t)
    return t[:8000] if len(t) > 8000 else t


def _contacts_for_campaign(campaign):
    q = Contact.query.filter_by(status="active")
    if campaign.contact_list_id:
        q = q.filter(Contact.contact_list_id == campaign.contact_list_id)
    return q.all()


def send_campaign_impl(campaign_id, skip_schedule_check=False):
    """
    Send emails for a campaign. Returns (dict, http_status).
    skip_schedule_check=True when invoked from process-due for due scheduled rows.
    """
    campaign = Campaign.query.get(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}, 404
    if campaign.status == "sent":
        return {"error": "Campaign already sent"}, 400
    if campaign.status == "sending":
        return {"error": "Campaign is already being sent"}, 409
    if (
        campaign.status == "scheduled"
        and campaign.scheduled_at
        and datetime.utcnow() < campaign.scheduled_at
        and not skip_schedule_check
    ):
        return {"error": "Campaign is scheduled for a future time"}, 400

    contacts = _contacts_for_campaign(campaign)
    if not contacts:
        return {"error": "No active contacts for this campaign/list"}, 400

    campaign.status = "sending"
    db.session.commit()

    sent_ok = 0
    bounced = 0
    errors = []
    inner = campaign.body_html or ""
    idx = 0
    n = len(contacts)
    disconnect_strikes = 0

    while idx < n:
        try:
            with mail.connect() as conn:
                while idx < n:
                    c = contacts[idx]
                    send_row = EmailSend(
                        id=str(uuid.uuid4()),
                        campaign_id=campaign.id,
                        contact_id=c.id,
                        sent_at=None,
                    )
                    db.session.add(send_row)
                    db.session.flush()
                    send_id = send_row.id
                    full_html = _wrap_campaign_html(inner, c, send_id)
                    full_html = rewrite_links_for_tracking(full_html, send_id)
                    plain = _plain_from_html(full_html)
                    msg = Message(
                        subject=campaign.subject or "No subject",
                        recipients=[c.email],
                        body=plain,
                        html=full_html,
                        sender=app.config["MAIL_USERNAME"],
                    )
                    try:
                        conn.send(msg)
                        send_row.sent_at = datetime.utcnow()
                        db.session.commit()
                        sent_ok += 1
                        idx += 1
                        disconnect_strikes = 0
                        time.sleep(1)
                    except smtplib.SMTPRecipientsRefused:
                        db.session.rollback()
                        cc = Contact.query.get(c.id)
                        if cc:
                            cc.status = "bounced"
                        db.session.commit()
                        bounced += 1
                        errors.append({"email": c.email, "error": "SMTPRecipientsRefused"})
                        idx += 1
                    except smtplib.SMTPServerDisconnected:
                        db.session.rollback()
                        disconnect_strikes += 1
                        if disconnect_strikes >= 2:
                            campaign.status = "draft"
                            db.session.commit()
                            return {
                                "msg": "SMTP server disconnected; partial send.",
                                "sent": sent_ok,
                                "bounced": bounced,
                                "errors": errors,
                            }, 500
                        break
        except smtplib.SMTPServerDisconnected:
            db.session.rollback()
            disconnect_strikes += 1
            if disconnect_strikes >= 2:
                campaign.status = "draft"
                db.session.commit()
                return {
                    "msg": "SMTP server disconnected.",
                    "sent": sent_ok,
                    "bounced": bounced,
                    "errors": errors,
                }, 500
            continue

    campaign.status = "sent"
    campaign.sent_at = datetime.utcnow()
    db.session.commit()
    return {
        "msg": f"Campaign sent to {sent_ok} contacts ({bounced} bounced).",
        "sent": sent_ok,
        "bounced": bounced,
        "errors": errors,
    }, 200


# --- PUBLIC TRACKING ---


@app.route("/unsubscribe/<contact_id>", methods=["GET"])
def unsubscribe(contact_id):
    c = Contact.query.get(contact_id)
    if not c:
        return "<p>Contact not found.</p>", 404
    c.status = "unsubscribed"
    db.session.commit()
    return (
        "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
        "<p>You have been unsubscribed.</p></body></html>"
    )


@app.route("/t/open/<send_id>", methods=["GET"])
def track_open(send_id):
    es = EmailSend.query.get(send_id)
    if es:
        ev = EmailEvent(
            id=str(uuid.uuid4()),
            email_send_id=es.id,
            event_type="opened",
        )
        db.session.add(ev)
        db.session.commit()
    return Response(PIXEL_GIF, mimetype="image/gif")


@app.route("/t/click/<send_id>", methods=["GET"])
def track_click(send_id):
    es = EmailSend.query.get(send_id)
    raw_next = request.args.get("next") or ""
    try:
        dest = unquote(raw_next)
    except Exception:
        dest = raw_next
    if es and _is_safe_redirect_url(dest):
        ev = EmailEvent(
            id=str(uuid.uuid4()),
            email_send_id=es.id,
            event_type="clicked",
        )
        db.session.add(ev)
        db.session.commit()
        return redirect(dest, code=302)
    if _is_safe_redirect_url(dest):
        return redirect(dest, code=302)
    return jsonify({"error": "Invalid redirect"}), 400


# --- AUTH ---


@app.route("/")
def index_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.route("/api/me", methods=["GET"])
def api_me():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False}), 401
    return jsonify(
        {
            "authenticated": True,
            "email": current_user.email,
            "role": current_user.role,
            "id": current_user.id,
        }
    )


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password) or not getattr(user, "is_active", True):
        return jsonify({"error": "Invalid credentials"}), 401
    login_user(user, remember=True)
    return jsonify({"msg": "OK", "email": user.email, "role": user.role})


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"msg": "Logged out"})


# --- PROTECTED API ---


@app.route("/stats", methods=["GET"])
@login_required
def get_stats():
    total = Contact.query.count()
    active = Contact.query.filter_by(status="active").count()
    unsub = Contact.query.filter_by(status="unsubscribed").count()
    return jsonify(
        {
            "total_contacts": total,
            "active_contacts": active,
            "unsubscribed": unsub,
        }
    )


@app.route("/reports/summary", methods=["GET"])
@login_required
def reports_summary():
    sent = EmailSend.query.filter(EmailSend.sent_at.isnot(None)).count()
    opened = EmailEvent.query.filter_by(event_type="opened").count()
    return jsonify({"sent": sent, "opened": opened})


@app.route("/reports/dashboard-metrics", methods=["GET"])
@login_required
def reports_dashboard_metrics():
    """Rates use unique sends that recorded at least one open/click vs delivered sends."""
    sent_rows = EmailSend.query.filter(EmailSend.sent_at.isnot(None)).all()
    sent_ids = [s.id for s in sent_rows]
    n = len(sent_ids)
    if n == 0:
        return jsonify(
            {
                "sent": 0,
                "unique_opens": 0,
                "unique_clicks": 0,
                "open_rate": 0.0,
                "click_rate": 0.0,
            }
        )
    uo = (
        db.session.query(EmailEvent.email_send_id)
        .filter(EmailEvent.email_send_id.in_(sent_ids), EmailEvent.event_type == "opened")
        .distinct()
        .count()
    )
    uc = (
        db.session.query(EmailEvent.email_send_id)
        .filter(EmailEvent.email_send_id.in_(sent_ids), EmailEvent.event_type == "clicked")
        .distinct()
        .count()
    )
    open_rate = round((uo / n) * 100.0, 2)
    click_rate = round((uc / n) * 100.0, 2)
    return jsonify(
        {
            "sent": n,
            "unique_opens": uo,
            "unique_clicks": uc,
            "open_rate": open_rate,
            "click_rate": click_rate,
        }
    )


@app.route("/reports/activity", methods=["GET"])
@login_required
def reports_activity():
    """Last 10 email open events for the live activity feed (M9)."""
    events = (
        EmailEvent.query.options(
            joinedload(EmailEvent.email_send).joinedload(EmailSend.contact)
        )
        .filter_by(event_type="opened")
        .order_by(EmailEvent.created_at.desc())
        .limit(10)
        .all()
    )
    items = []
    for ev in events:
        es = ev.email_send
        contact = es.contact if es else None
        email = contact.email if contact else "(unknown)"
        ts = ev.created_at.isoformat() + "Z" if ev.created_at else ""
        items.append(
            {
                "email": email,
                "event_type": ev.event_type,
                "created_at": ts,
                "line": f"Email opened by {email} at {ts}",
            }
        )
    return jsonify({"items": items})


@app.route("/contacts", methods=["GET"])
@login_required
def list_contacts():
    """Segmented contact list with optional status filter and search (M2)."""
    status = (request.args.get("status") or "").strip().lower()
    status_in = (request.args.get("status_in") or "").strip()
    list_id = (request.args.get("contact_list_id") or "").strip()
    q = (request.args.get("q") or "").strip()

    query = Contact.query.options(joinedload(Contact.contact_list)).order_by(Contact.email)
    if list_id:
        query = query.filter(Contact.contact_list_id == list_id)
    if status_in:
        parts = [s.strip().lower() for s in status_in.split(",") if s.strip()]
        allowed = {"active", "unsubscribed", "bounced"}
        parts = [p for p in parts if p in allowed]
        if parts:
            query = query.filter(Contact.status.in_(parts))
    elif status in ("active", "unsubscribed", "bounced"):
        query = query.filter(Contact.status == status)
    if q:
        pat = f"%{q}%"
        query = query.filter(
            or_(
                Contact.email.ilike(pat),
                Contact.first_name.ilike(pat),
                Contact.last_name.ilike(pat),
            )
        )
    rows = query.limit(500).all()
    out = []
    for c in rows:
        list_name = c.contact_list.name if c.contact_list else None
        out.append(
            {
                "id": c.id,
                "email": c.email,
                "first_name": c.first_name or "",
                "last_name": c.last_name or "",
                "status": c.status,
                "contact_list_id": c.contact_list_id,
                "contact_list_name": list_name or "—",
            }
        )
    return jsonify({"contacts": out})


@app.route("/contacts/<cid>", methods=["PATCH"])
@login_required
def patch_contact(cid):
    c = Contact.query.get(cid)
    if not c:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    if "status" in data:
        st = (data.get("status") or "").strip().lower()
        if st in ("active", "unsubscribed", "bounced"):
            c.status = st
    db.session.commit()
    return jsonify({"id": c.id, "status": c.status})


@app.route("/contact-lists", methods=["GET"])
@login_required
def contact_lists():
    rows = ContactList.query.order_by(ContactList.name).all()
    return jsonify([{"id": r.id, "name": r.name} for r in rows])


@app.route("/contact-lists/summary", methods=["GET"])
@login_required
def contact_lists_summary():
    rows = (
        db.session.query(ContactList.id, ContactList.name, func.count(Contact.id))
        .outerjoin(Contact, Contact.contact_list_id == ContactList.id)
        .group_by(ContactList.id)
        .order_by(ContactList.name)
        .all()
    )
    return jsonify([{"id": r[0], "name": r[1], "contact_count": r[2]} for r in rows])


@app.route("/segments/preview", methods=["GET"])
@login_required
def segments_preview():
    st = (request.args.get("status") or "active").strip().lower()
    if st not in ("active", "unsubscribed", "bounced"):
        st = "active"
    cnt = Contact.query.filter_by(status=st).count()
    return jsonify({"status": st, "count": cnt})


@app.route("/import", methods=["POST"])
@login_required
def import_contacts():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    list_name = (request.form.get("list_name") or "Default").strip() or "Default"
    cl = ContactList.query.filter_by(name=list_name).first()
    if not cl:
        cl = ContactList(name=list_name)
        db.session.add(cl)
        db.session.flush()

    file = request.files["file"]
    try:
        df = pd.read_csv(file)
        added, skipped = 0, 0
        for _, row in df.iterrows():
            email = str(row["email"]).strip().lower()
            if not Contact.query.filter_by(email=email).first():
                fn = "Subscriber"
                if "first_name" in df.columns and pd.notna(row.get("first_name")):
                    fn = str(row["first_name"]).strip() or "Subscriber"
                ln = ""
                if "last_name" in df.columns and pd.notna(row.get("last_name")):
                    ln = str(row["last_name"]).strip()
                contact = Contact(
                    email=email,
                    first_name=fn,
                    last_name=ln,
                    contact_list_id=cl.id,
                )
                db.session.add(contact)
                added += 1
            else:
                skipped += 1

        db.session.commit()
        return jsonify({"msg": f"Import Complete: {added} added, {skipped} skipped (list: {list_name})."}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/templates", methods=["GET", "POST"])
@login_required
def templates():
    if request.method == "GET":
        rows = Template.query.order_by(Template.created_at.desc()).all()
        return jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "html_body": t.html_body,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in rows
            ]
        )
    data = request.get_json() or {}
    name = (data.get("name") or "Untitled").strip()
    html_body = data.get("html_body") or ""
    t = Template(name=name, html_body=html_body)
    db.session.add(t)
    db.session.commit()
    return jsonify({"id": t.id, "name": t.name}), 201


@app.route("/templates/<tid>", methods=["GET", "PATCH"])
@login_required
def template_one(tid):
    t = Template.query.get(tid)
    if not t:
        return jsonify({"error": "Not found"}), 404
    if request.method == "GET":
        return jsonify(
            {
                "id": t.id,
                "name": t.name,
                "html_body": t.html_body,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )
    data = request.get_json() or {}
    if "name" in data:
        t.name = (data.get("name") or "").strip() or t.name
    if "html_body" in data:
        t.html_body = data.get("html_body") or ""
    db.session.commit()
    return jsonify({"id": t.id, "name": t.name})


STARTER_TEMPLATES = [
    ("Starter: Announcement", "<p><strong>News:</strong> Your message here.</p>"),
    ("Starter: Newsletter", "<p>Hi {{first_name}},</p><p>This week’s highlights…</p>"),
    ("Starter: Promotion", "<p>Limited time offer — <a href=\"https://example.com\">Learn more</a></p>"),
    ("Starter: Plain text style", "<p>Simple, clean paragraph. Reply if you have questions.</p>"),
    ("Starter: Two columns", "<table width=\"100%\"><tr><td>Left</td><td>Right</td></tr></table>"),
]


@app.route("/templates/seed-starters", methods=["POST"])
@login_required
def templates_seed_starters():
    if Template.query.count() > 0:
        return jsonify({"msg": "Templates already exist; clear DB or delete templates to seed.", "created": 0})
    for name, html in STARTER_TEMPLATES:
        db.session.add(Template(name=name, html_body=html))
    db.session.commit()
    return jsonify({"created": len(STARTER_TEMPLATES)})


@app.route("/organization/settings", methods=["GET", "PATCH"])
@login_required
def organization_settings():
    row = OrganizationSettings.query.first()
    if not row:
        row = OrganizationSettings()
        db.session.add(row)
        db.session.commit()
    if request.method == "GET":
        return jsonify(
            {
                "org_name": row.org_name,
                "logo_url": row.logo_url or "",
                "default_from_name": row.default_from_name or "",
                "default_from_email": row.default_from_email or "",
                "aws_ses_region": row.aws_ses_region or "",
                "aws_ses_note": row.aws_ses_note or "",
            }
        )
    data = request.get_json() or {}
    for fld in (
        "org_name",
        "logo_url",
        "default_from_name",
        "default_from_email",
        "aws_ses_region",
        "aws_ses_note",
    ):
        if fld in data and data.get(fld) is not None:
            setattr(row, fld, str(data.get(fld) or "").strip())
    db.session.commit()
    return jsonify({"msg": "Saved"})


@app.route("/api/users", methods=["GET", "POST"])
@login_required
def api_users():
    if current_user.role != "Admin":
        return jsonify({"error": "Forbidden"}), 403
    if request.method == "GET":
        users = User.query.order_by(User.email).all()
        return jsonify(
            [
                {
                    "id": u.id,
                    "email": u.email,
                    "role": u.role,
                    "is_active": getattr(u, "is_active", True),
                }
                for u in users
            ]
        )
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or uuid.uuid4().hex[:12]
    role = (data.get("role") or "Manager").strip()
    if role not in ("Admin", "Manager"):
        role = "Manager"
    if not email:
        return jsonify({"error": "email required"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "User exists"}), 400
    u = User(email=email, role=role, is_active=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify({"id": u.id, "email": u.email, "temporary_password": password}), 201


@app.route("/api/users/<uid>", methods=["PATCH"])
@login_required
def api_users_patch(uid):
    if current_user.role != "Admin":
        return jsonify({"error": "Forbidden"}), 403
    u = User.query.get(uid)
    if not u:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    if u.id == current_user.id and data.get("is_active") is False:
        return jsonify({"error": "Cannot deactivate yourself"}), 400
    if "role" in data:
        r = (data.get("role") or "").strip()
        if r in ("Admin", "Manager"):
            u.role = r
    if "is_active" in data:
        u.is_active = bool(data.get("is_active"))
    db.session.commit()
    return jsonify({"id": u.id, "role": u.role, "is_active": u.is_active})


@app.route("/campaigns", methods=["GET", "POST"])
@login_required
def campaigns():
    if request.method == "GET":
        rows = Campaign.query.order_by(Campaign.created_at.desc()).all()
        return jsonify(
            [
                {
                    "id": c.id,
                    "name": c.name,
                    "subject": c.subject,
                    "status": c.status,
                    "scheduled_at": c.scheduled_at.isoformat() + "Z" if c.scheduled_at else None,
                    "sent_at": c.sent_at.isoformat() + "Z" if c.sent_at else None,
                    "contact_list_id": c.contact_list_id,
                    "template_id": c.template_id,
                }
                for c in rows
            ]
        )
    data = request.get_json() or {}
    c = Campaign(
        name=(data.get("name") or "Campaign").strip(),
        subject=(data.get("subject") or "").strip(),
        body_html=data.get("body_html") or "",
        status="draft",
        contact_list_id=data.get("contact_list_id") or None,
        template_id=data.get("template_id") or None,
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({"id": c.id}), 201


@app.route("/campaigns/<cid>", methods=["GET", "PATCH"])
@login_required
def campaign_one(cid):
    c = Campaign.query.get(cid)
    if not c:
        return jsonify({"error": "Not found"}), 404
    if request.method == "GET":
        return jsonify(
            {
                "id": c.id,
                "name": c.name,
                "subject": c.subject,
                "body_html": c.body_html,
                "status": c.status,
                "scheduled_at": c.scheduled_at.isoformat() + "Z" if c.scheduled_at else None,
                "contact_list_id": c.contact_list_id,
                "template_id": c.template_id,
            }
        )
    data = request.get_json() or {}
    if "name" in data:
        c.name = (data.get("name") or "").strip() or c.name
    if "subject" in data:
        c.subject = (data.get("subject") or "").strip()
    if "body_html" in data:
        c.body_html = data.get("body_html") or ""
    if "contact_list_id" in data:
        c.contact_list_id = data.get("contact_list_id") or None
    if "template_id" in data:
        c.template_id = data.get("template_id") or None
    db.session.commit()
    return jsonify({"id": c.id})


@app.route("/campaigns/<cid>/report", methods=["GET"])
@login_required
def campaign_report(cid):
    c = Campaign.query.get(cid)
    if not c:
        return jsonify({"error": "Not found"}), 404
    sends = (
        EmailSend.query.filter_by(campaign_id=cid).filter(EmailSend.sent_at.isnot(None)).all()
    )
    send_ids = [s.id for s in sends]
    total = len(send_ids)
    if total == 0:
        return jsonify(
            {
                "campaign_id": cid,
                "name": c.name,
                "subject": c.subject,
                "sent": 0,
                "unique_opens": 0,
                "unique_clicks": 0,
                "open_rate": 0.0,
                "click_rate": 0.0,
            }
        )
    uo = (
        db.session.query(EmailEvent.email_send_id)
        .filter(EmailEvent.email_send_id.in_(send_ids), EmailEvent.event_type == "opened")
        .distinct()
        .count()
    )
    uc = (
        db.session.query(EmailEvent.email_send_id)
        .filter(EmailEvent.email_send_id.in_(send_ids), EmailEvent.event_type == "clicked")
        .distinct()
        .count()
    )
    return jsonify(
        {
            "campaign_id": cid,
            "name": c.name,
            "subject": c.subject,
            "sent": total,
            "unique_opens": uo,
            "unique_clicks": uc,
            "open_rate": round((uo / total) * 100.0, 2),
            "click_rate": round((uc / total) * 100.0, 2),
        }
    )


@app.route("/campaigns/<cid>/schedule", methods=["POST"])
@login_required
def campaign_schedule(cid):
    c = Campaign.query.get(cid)
    if not c:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    iso = data.get("scheduled_at")
    if not iso:
        return jsonify({"error": "scheduled_at required (ISO 8601)"}), 400
    try:
        s = iso
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return jsonify({"error": "Invalid scheduled_at"}), 400
    c.scheduled_at = dt
    c.status = "scheduled" if datetime.utcnow() < dt else "draft"
    db.session.commit()
    return jsonify({"id": c.id, "status": c.status, "scheduled_at": iso})


@app.route("/campaigns/<cid>/send-now", methods=["POST"])
@login_required
def campaign_send_now(cid):
    body, status = send_campaign_impl(cid, skip_schedule_check=False)
    return jsonify(body), status


@app.route("/campaigns/process-due", methods=["POST"])
@login_required
def campaigns_process_due():
    now = datetime.utcnow()
    due = Campaign.query.filter(
        Campaign.status == "scheduled",
        Campaign.scheduled_at.isnot(None),
        Campaign.scheduled_at <= now,
    ).all()
    results = []
    for c in due:
        payload, code = send_campaign_impl(c.id, skip_schedule_check=True)
        results.append({"id": c.id, "code": code, **payload})
    return jsonify({"processed": len(results), "results": results})


@app.route("/send-campaign", methods=["POST"])
@login_required
def send_campaign_legacy():
    """Backward-compatible: create a one-off campaign and send immediately."""
    data = request.json or {}
    subject = data.get("subject") or "Campaign"
    body = data.get("body") or ""
    inner_html = "<p>" + html_lib.escape(body).replace("\n", "<br>\n") + "</p>"
    c = Campaign(
        name="Quick send",
        subject=subject,
        body_html=inner_html,
        status="draft",
    )
    db.session.add(c)
    db.session.commit()
    payload, status = send_campaign_impl(c.id, skip_schedule_check=True)
    return jsonify(payload), status


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        _ensure_sqlite_schema()
        _bootstrap_admin()
        _bootstrap_org_settings()
    app.run(debug=True)
