"""Career Coach - resume vs Naukri job description skill-gap tool.

Run:  C:\\Users\\tejas\\career-coach\\START.bat
Open: http://127.0.0.1:5055
"""
import io
import json
import os
import re
import shutil
import sqlite3
import datetime as dt

from flask import Flask, request, redirect, url_for, render_template, flash

from skills_db import SKILLS, ROLE_LABELS

BASE = os.path.dirname(os.path.abspath(__file__))
# On Render the writable spot is a temp dir; locally it's the project folder.
DATA = os.environ.get("CAREER_DATA_DIR", BASE)
UPLOADS = os.path.join(DATA, "uploads")
DB = os.path.join(DATA, "career.db")
os.makedirs(UPLOADS, exist_ok=True)

# PIN lock: only needed when the app is public on the internet.
APP_PIN = os.environ.get("APP_PIN", "").strip()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "career-coach-local")
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024  # 40 MB


@app.before_request
def require_pin():
    """When APP_PIN is set (cloud), demand it once per browser session."""
    from flask import session
    if not APP_PIN:
        return None
    if request.endpoint in ("login", "static") or request.path.startswith("/static"):
        return None
    if session.get("ok"):
        return None
    return render_template("login.html"), 401


@app.route("/login", methods=["GET", "POST"])
def login():
    from flask import session
    if not APP_PIN:
        return redirect(url_for("home"))
    if request.method == "POST":
        if (request.form.get("pin") or "").strip() == APP_PIN:
            session["ok"] = True
            session.permanent = True
            return redirect(url_for("home"))
        flash("Wrong PIN.", "bad")
    return render_template("login.html")


# --------------------------------------------------------------- database
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS resume(
            id INTEGER PRIMARY KEY CHECK (id = 1),
            filename TEXT, text TEXT, uploaded TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS jd(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, role TEXT, source TEXT, jd_text TEXT,
            report TEXT, created TEXT)""")


def get_resume():
    with db() as c:
        return c.execute("SELECT * FROM resume WHERE id=1").fetchone()


# Create tables at import time so the app works under gunicorn/Render too
# (gunicorn imports app:app and never runs the __main__ block below).
init_db()


# --------------------------------------------------------------- text extraction
def ocr_available():
    import pytesseract
    for p in (shutil.which("tesseract"),
              "/usr/bin/tesseract",
              r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")):
        if p and os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            return True
    return False


def extract_text(storage):
    """Return (text, note) from an uploaded file: pdf / docx / txt / image."""
    name = (storage.filename or "file").lower()
    raw = storage.read()
    if not raw:
        return "", "empty file"

    if name.endswith(".pdf"):
        import pymupdf
        doc = pymupdf.open(stream=raw, filetype="pdf")
        txt = "\n".join(p.get_text() for p in doc)
        if len(txt.strip()) > 40:
            return txt, "PDF text layer"
        # scanned pdf -> OCR each page
        if ocr_available():
            import pytesseract
            from PIL import Image
            out = []
            for p in doc:
                pix = p.get_pixmap(dpi=200)
                out.append(pytesseract.image_to_string(
                    Image.open(io.BytesIO(pix.tobytes("png")))))
            return "\n".join(out), "scanned PDF via OCR"
        return txt, "PDF had no text layer and OCR is not installed"

    if name.endswith((".docx",)):
        import docx
        d = docx.Document(io.BytesIO(raw))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.extend(cell.text for cell in row.cells)
        return "\n".join(parts), "Word document"

    if name.endswith((".txt", ".md")):
        return raw.decode("utf-8", "ignore"), "text file"

    if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")):
        if not ocr_available():
            return "", ("Tesseract OCR is not installed yet, so I cannot read text out of "
                        "screenshots. Paste the job text into the box instead.")
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) < 1400:                      # upscale small phone shots
            f = 1400 / max(img.size)
            img = img.resize((int(img.width * f), int(img.height * f)))
        return pytesseract.image_to_string(img), "screenshot via OCR"

    return "", f"unsupported file type: {name}"


# --------------------------------------------------------------- matching engine
def norm(t):
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9&+/. ]+", " ", t)
    return re.sub(r"\s+", " ", t)


def hits(alias, text):
    """Word-boundary alias search; short aliases must stand alone."""
    a = norm(alias)
    if not a:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", text) is not None


def detect_role(text):
    """Title line carries most weight, then the body."""
    t = norm(text)
    head = norm("\n".join(text.strip().splitlines()[:3])) if text.strip() else ""
    pats = {
        "rsm": ["regional sales", "sales manager", "area sales", "territory",
                "distributor", "revenue target", "channel sales"],
        "training manager": ["training manager", "manager training", "training head",
                             "capability manager", "manager learning and development",
                             "assistant manager training", "deputy manager training"],
        "l&d": ["l d manager", "learning and development", "learning development",
                "organisational development", "l d specialist", "l d executive"],
        "trainer": ["trainer", "facilitator", "process trainer", "soft skill",
                    "voice and accent", "instructional designer"],
    }
    score = {}
    for role, words in pats.items():
        score[role] = sum(5 * head.count(w) + t.count(w) for w in words)
    best = max(score, key=score.get)
    return best if score[best] else "training manager"


def analyse(jd_text, resume_text):
    jd, res = norm(jd_text), norm(resume_text)
    role = detect_role(jd_text)

    asked, gaps, have = [], [], []
    for s in SKILLS:
        in_jd = any(hits(a, jd) for a in s["aliases"]) or hits(s["key"], jd)
        if not in_jd:
            continue
        in_res = any(hits(a, res) for a in s["aliases"]) or hits(s["key"], res)
        rec = dict(key=s["key"], why=s["why"], learn=s["learn"], proof=s["proof"],
                   matched=[a for a in s["aliases"] if hits(a, jd)][:6])
        asked.append(rec)
        (have if in_res else gaps).append(rec)

    # role-critical skills the JD implies even if not literally worded
    implied = []
    for s in SKILLS:
        if role in s["roles"] and s["key"] not in [a["key"] for a in asked]:
            if not (any(hits(a, res) for a in s["aliases"]) or hits(s["key"], res)):
                implied.append(dict(key=s["key"], why=s["why"], learn=s["learn"],
                                    proof=s["proof"], matched=[]))

    total = len(asked) or 1
    return dict(role=role, role_label=ROLE_LABELS[role],
                match_pct=round(100 * len(have) / total),
                have=have, gaps=gaps, implied=implied[:6],
                asked_count=len(asked),
                generated=dt.datetime.now().strftime("%d %b %Y, %I:%M %p"))


# --------------------------------------------------------------- routes
@app.route("/")
def home():
    with db() as c:
        jds = c.execute("SELECT id,title,role,created,report FROM jd ORDER BY id DESC LIMIT 25").fetchall()
    rows = []
    for j in jds:
        r = json.loads(j["report"])
        rows.append(dict(id=j["id"], title=j["title"], role=r["role_label"],
                         created=j["created"], pct=r["match_pct"], gaps=len(r["gaps"])))
    return render_template("home.html", resume=get_resume(), jds=rows,
                           ocr=ocr_available())


@app.route("/resume", methods=["POST"])
def upload_resume():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Pick your resume file first.", "bad")
        return redirect(url_for("home"))
    text, note = extract_text(f)
    if len(text.strip()) < 50:
        flash(f"Could not read that resume ({note}). Try a PDF or DOCX.", "bad")
        return redirect(url_for("home"))
    path = os.path.join(UPLOADS, "resume_" + re.sub(r"[^A-Za-z0-9._-]", "_", f.filename))
    f.seek(0)
    with open(path, "wb") as out:
        out.write(f.read())
    with db() as c:
        c.execute("INSERT OR REPLACE INTO resume(id,filename,text,uploaded) VALUES(1,?,?,?)",
                  (f.filename, text, dt.datetime.now().strftime("%d %b %Y, %I:%M %p")))
    flash(f"Resume saved ({note}, {len(text.split())} words). It stays saved - no need to upload again.", "good")
    return redirect(url_for("home"))


@app.route("/analyse", methods=["POST"])
def do_analyse():
    r = get_resume()
    if not r:
        flash("Upload your resume once first.", "bad")
        return redirect(url_for("home"))

    chunks, notes = [], []
    for f in request.files.getlist("shots"):
        if f and f.filename:
            t, n = extract_text(f)
            chunks.append(t)
            notes.append(f"{f.filename}: {n}")
    pasted = (request.form.get("pasted") or "").strip()
    if pasted:
        chunks.append(pasted)
        notes.append("pasted text")

    jd_text = "\n\n".join(x for x in chunks if x).strip()
    if len(jd_text) < 60:
        flash("I couldn't get enough job-description text. "
              + (" | ".join(notes) if notes else "Upload a screenshot or paste the text."), "bad")
        return redirect(url_for("home"))

    rep = analyse(jd_text, r["text"])
    rep["sources"] = notes
    title = (request.form.get("title") or "").strip() or (
        jd_text.strip().splitlines()[0][:70] if jd_text.strip() else "Untitled job")
    with db() as c:
        cur = c.execute("INSERT INTO jd(title,role,source,jd_text,report,created) VALUES(?,?,?,?,?,?)",
                        (title, rep["role"], " | ".join(notes), jd_text, json.dumps(rep),
                         dt.datetime.now().strftime("%d %b %Y, %I:%M %p")))
        new_id = cur.lastrowid
    return redirect(url_for("report", jd_id=new_id))


@app.route("/report/<int:jd_id>")
def report(jd_id):
    with db() as c:
        row = c.execute("SELECT * FROM jd WHERE id=?", (jd_id,)).fetchone()
    if not row:
        flash("That report is gone.", "bad")
        return redirect(url_for("home"))
    return render_template("report.html", jd=row, r=json.loads(row["report"]))


@app.route("/delete/<int:jd_id>", methods=["POST"])
def delete(jd_id):
    with db() as c:
        c.execute("DELETE FROM jd WHERE id=?", (jd_id,))
    return redirect(url_for("home"))


def resume_draft(rep, jd_text, resume_text):
    """Build ready-to-paste resume wording tuned to this JD.

    Honesty rule: skills you already have -> reworded in JD language now.
    Skills you don't -> parked in a 'add this line the day you finish it' list,
    never silently claimed.
    """
    jd = norm(jd_text)
    role_label = rep["role_label"]

    # Naukri headline: role title + your real anchors + top JD keywords you own
    owned = [h["key"] for h in rep["have"]]
    headline = (f"{role_label} | 12-member trainer team | NHT & Technical Training owner | "
                + ", ".join(k.split(" (")[0] for k in owned[:3]))

    # Key Skills box on Naukri = exact JD keywords. Recruiter search runs on this.
    kw = []
    JUNK = {"team of", "coordinate a team", "od", "gt", "mt", "id", "pip", "sop", "ai"}
    for s in SKILLS:
        for a in s["aliases"]:
            if hits(a, jd) and len(a) > 3 and a.lower() not in JUNK:
                kw.append(a.title() if a.islower() else a)
    seen, keywords = set(), []
    for k in kw:
        if k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k)

    summary = (f"Training operations professional targeting {role_label} roles. "
               f"Own a 12-day New Hire Training curriculum and a 3-day technical training track "
               f"for a 12-member trainer team, with daily activity governance, MIS reporting and "
               f"audit-clean training records. Strengths this role asks for: "
               + "; ".join(k.split(" (")[0] for k in owned[:5]) + ".")

    # Rewrite existing strengths in the JD's own language
    now = [dict(skill=h["key"], bullet=RESUME_LINES.get(h["key"], h["proof"])) for h in rep["have"]]
    later = [dict(skill=g["key"], bullet=RESUME_LINES.get(g["key"], g["proof"]))
             for g in rep["gaps"] + rep["implied"]]

    return dict(headline=headline[:250], summary=summary,
                keywords=keywords[:25], now=now, later=later)


# Resume-ready phrasing per skill: what the bullet should literally say.
RESUME_LINES = {
    "ADDIE instructional design":
        "Designed and rebuilt the 12-day NHT curriculum using the ADDIE model - needs analysis, "
        "module design, content development, rollout and post-batch evaluation.",
    "Kirkpatrick training evaluation (L1-L4)":
        "Measured training effectiveness on Kirkpatrick L1-L4: feedback scores, assessment pass %, "
        "on-floor behaviour audits and business-metric impact reported to leadership.",
    "TNI / TNA (Training Needs Identification)":
        "Ran Training Needs Identification across a 12-trainer team using performance data, "
        "manager inputs and skill surveys; converted findings into a quarterly training calendar.",
    "LMS administration":
        "Administered the LMS end to end - course setup, batch enrolment, completion tracking "
        "and compliance/certification reporting.",
    "e-learning authoring (Articulate / Rise / Captivate)":
        "Built digital learning modules (Articulate Rise 360 / Storyline) converting classroom "
        "NHT content into self-paced e-learning with assessments.",
    "Train-the-Trainer / facilitation certification":
        "Certified Train-the-Trainer; facilitate classroom and virtual sessions and certify new "
        "trainers through structured demo evaluation.",
    "Coaching & feedback models (GROW, SBI)":
        "Coach trainers one-on-one using the GROW model and SBI feedback; improved "
        "underperformer output through structured monthly capability reviews.",
    "Content / SOP documentation":
        "Authored one-page SOPs and job aids for recurring processes (MIS Update, Attendance "
        "Regularization, Visit Barge / New Process), reducing repeat queries.",
    "Excel advanced (pivots, lookups, dashboards)":
        "Advanced Excel - pivot tables, XLOOKUP/VLOOKUP and slicer-driven dashboards used for "
        "daily trainer activity MIS across a 12-member team.",
    "Power BI / data visualisation":
        "Publish weekly Power BI dashboards on training throughput, attendance and certification "
        "status for management review.",
    "Learning analytics / MIS reporting":
        "Own training MIS and learning analytics - batch throughput, certification pass rate, "
        "TAT and attrition linked to training - reported in weekly and monthly reviews.",
    "Sales target ownership & pipeline management":
        "Carried and delivered a quarterly target of <fill in number>; managed pipeline coverage "
        "and conversion against plan.",
    "Channel / distributor management":
        "Managed channel/distributor partners across the territory - coverage planning, beat "
        "plans and partner ROI reviews.",
    "CRM tools (Salesforce / Zoho / LeadSquared)":
        "Hands-on with CRM (Salesforce/Zoho) for lead stages, activity logging and pipeline "
        "reporting; Salesforce Trailhead certified badges.",
    "Team leadership & performance management":
        "Coordinate a 12-member trainer team - rostering, daily activity governance, capability "
        "reviews and performance conversations.",
    "Stakeholder / business partnering":
        "Partner with business stakeholders and senior management; own the weekly/monthly training "
        "review deck and governance cadence.",
    "Onboarding / new hire training programme design":
        "Own the 12-day New Hire Training programme and 3-day Technical Training track - "
        "objectives, assessments, certification gates and ramp-up tracking.",
    "Compliance / audit readiness of training records":
        "Maintain audit-ready training records and attendance regularization; closed audit cycles "
        "with zero documentation observations.",
    "Communication & business English / presentation":
        "Strong classroom facilitation, business communication and presentation skills; deliver "
        "leadership-facing review presentations.",
    "AI tools for L&D (ChatGPT/Gemini for content)":
        "Use AI tools (ChatGPT/Gemini/Copilot) to draft assessments, role-play scripts and "
        "training content, cutting content development time significantly.",
    "Vendor & training budget management":
        "Manage training budget and external vendor partners; track cost per trainee against plan.",
}


@app.route("/resume-draft/<int:jd_id>")
def resume_draft_page(jd_id):
    with db() as c:
        row = c.execute("SELECT * FROM jd WHERE id=?", (jd_id,)).fetchone()
    r = get_resume()
    if not row or not r:
        flash("Need a saved resume and a job first.", "bad")
        return redirect(url_for("home"))
    rep = json.loads(row["report"])
    return render_template("draft.html", jd=row, r=rep,
                           d=resume_draft(rep, row["jd_text"], r["text"]))


@app.route("/icon-192.png")
def icon192():
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE, "static"), "icon-192.png")


@app.route("/icon-512.png")
def icon512():
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE, "static"), "icon-512.png")


def lan_ip():
    """Best-guess LAN IP so the phone can reach this laptop."""
    import socket as sk
    try:
        s = sk.socket(sk.AF_INET, sk.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.route("/qr.png")
def qr_png():
    """QR code of the phone URL so you can just scan it."""
    import qrcode
    from flask import Response
    img = qrcode.make(f"http://{lan_ip()}:5055/")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png")


@app.route("/phone")
def phone():
    return render_template("phone.html", ip=lan_ip(), port=5055)


@app.route("/backup")
def backup():
    """Download everything (resume text + all reports) as one JSON file.

    Needed because Render's free plan erases files when the app redeploys or
    restarts. Keep the downloaded file; restore it below in one click.
    """
    from flask import Response
    with db() as c:
        r = c.execute("SELECT filename,text,uploaded FROM resume WHERE id=1").fetchone()
        jds = c.execute("SELECT title,role,source,jd_text,report,created FROM jd").fetchall()
    blob = dict(resume=(dict(r) if r else None), jobs=[dict(x) for x in jds],
                saved=dt.datetime.now().isoformat())
    return Response(json.dumps(blob, indent=1),
                    mimetype="application/json",
                    headers={"Content-Disposition":
                             "attachment; filename=career-coach-backup.json"})


@app.route("/restore", methods=["POST"])
def restore():
    f = request.files.get("bak")
    if not f or not f.filename:
        flash("Pick your backup file first.", "bad")
        return redirect(url_for("home"))
    try:
        blob = json.loads(f.read().decode("utf-8", "ignore"))
    except Exception:
        flash("That file isn't a valid backup.", "bad")
        return redirect(url_for("home"))
    with db() as c:
        if blob.get("resume"):
            b = blob["resume"]
            c.execute("INSERT OR REPLACE INTO resume(id,filename,text,uploaded) VALUES(1,?,?,?)",
                      (b.get("filename"), b.get("text"), b.get("uploaded")))
        for j in blob.get("jobs", []):
            c.execute("INSERT INTO jd(title,role,source,jd_text,report,created) VALUES(?,?,?,?,?,?)",
                      (j.get("title"), j.get("role"), j.get("source"), j.get("jd_text"),
                       j.get("report"), j.get("created")))
    flash(f"Restored your resume and {len(blob.get('jobs', []))} job(s).", "good")
    return redirect(url_for("home"))


@app.route("/plan")
def plan():
    """Combined learning plan across every job analysed so far."""
    with db() as c:
        rows = c.execute("SELECT report FROM jd").fetchall()
    freq = {}
    for row in rows:
        rep = json.loads(row["report"])
        for g in rep["gaps"] + rep["implied"]:
            e = freq.setdefault(g["key"], dict(g, n=0))
            e["n"] += 1
    ranked = sorted(freq.values(), key=lambda x: -x["n"])
    return render_template("plan.html", ranked=ranked, jobs=len(rows))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5055))
    print(f"\n  Career Coach running -> http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
