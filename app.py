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

# Optional AI analysis layer (Gemini). Falls back to the rule-based engine if no key.
import llm as _llm
llm_analyse = _llm.analyse
llm_clearance = _llm.clearance_plan_from_ai

BASE = os.path.dirname(os.path.abspath(__file__))
# On Render the writable spot is a temp dir; locally it's the project folder.
DATA = os.environ.get("CAREER_DATA_DIR", BASE)
UPLOADS = os.path.join(DATA, "uploads")
DB = os.path.join(DATA, "career.db")
# Plain-text mirror of the saved resume. The SQLite DB (career.db) lives on a
# disk that Render's free plan wipes on every restart, which is exactly why the
# app kept demanding "upload your resume again". The mirror survives restarts,
# so on boot we re-seed the DB from it -> your resume is remembered for good.
RESUME_MIRROR = os.path.join(DATA, "resume_mirror.txt")
os.makedirs(UPLOADS, exist_ok=True)

# PIN lock: only needed when the app is public on the internet.
APP_PIN = os.environ.get("APP_PIN", "").strip()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "career-coach-local")
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024  # 40 MB


@app.route("/health")
def health():
    """Lightweight status check - reports whether screenshot OCR is available
    on this deployment (Tesseract installed or not). No auth needed."""
    return {"ok": True, "ocr": ocr_available(),
            "role_count": len(SKILLS)}


@app.route("/ai-status")
def ai_status_route():
    """Reports whether the AI layer is active and, if not, exactly why.
    Useful for debugging 'rule-based mode' without guessing."""
    from llm import ai_status
    return ai_status()


@app.route("/ai-test")
def ai_test():
    """Temporary diagnostic: runs the real llm.analyse() with a cross-field
    SQL JD vs a non-technical resume and dumps the raw model response."""
    import llm, traceback
    jd = ("We are hiring a Database Developer. Required: strong SQL, PL/SQL, "
          "Oracle, ETL pipelines, data modelling, performance tuning. 5+ years.")
    res = ("I am a Training Manager with 8 years in L&D. Skills: ADDIE, "
           "Kirkpatrick, TNI, facilitation, coaching.")
    try:
        prov = llm.provider()
        st = llm.ai_status()
        # raw call to see exactly what the model returns
        raw = None
        raw_err = None
        try:
            raw = llm._call(llm._PROMPT.format(resume_text=res, jd_text=jd))
        except Exception as e:
            raw_err = f"{type(e).__name__}: {e}"
        parsed = llm._extract_json(raw) if raw else None
        rep = llm.analyse(jd, res)
        return {"provider": prov, "ai_status": st,
                "raw_err": raw_err,
                "raw_len": len(raw) if raw else 0,
                "raw_head": (raw[:500] if raw else None),
                "parsed_ok": bool(parsed),
                "parsed_keys": list(parsed.keys()) if isinstance(parsed, dict) else None,
                "returned": "AI dict" if rep else "None (fell back to rule-based)",
                "role": rep.get("role_label") if rep else None,
                "match": rep.get("match_pct") if rep else None}
    except Exception as e:
        return {"provider": llm.provider(), "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-800:]}


@app.route("/env-debug")
def env_debug():
    """Temporary: list env var NAMES containing KEY/API/AI/GROQ (values hidden)
    so we can see exactly what Render passed without leaking secrets."""
    import os
    names = [k for k in os.environ
             if any(s in k.upper() for s in ("KEY", "API", "AI_", "GROQ", "GEMINI", "OPENAI"))]
    return {"env_var_names_seen": sorted(names)}


@app.before_request
def require_pin():
    """When APP_PIN is set (cloud), demand it once per browser session."""
    from flask import session
    if not APP_PIN:
        return None
    if request.endpoint in ("login", "static", "health") or request.path.startswith("/static"):
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
    # Re-seed the resume from the mirror so a Render restart never asks the
    # user to upload their resume again.
    if not get_resume() and os.path.exists(RESUME_MIRROR):
        try:
            payload = json.loads(open(RESUME_MIRROR, encoding="utf-8").read())
            with db() as c:
                c.execute("INSERT OR REPLACE INTO resume(id,filename,text,uploaded) VALUES(1,?,?,?)",
                          (payload.get("filename"), payload.get("text"), payload.get("uploaded")))
        except Exception:
            pass


def set_resume(filename, text, uploaded):
    """Persist the resume in BOTH the DB and the mirror file."""
    with db() as c:
        c.execute("INSERT OR REPLACE INTO resume(id,filename,text,uploaded) VALUES(1,?,?,?)",
                  (filename, text, uploaded))
    try:
        with open(RESUME_MIRROR, "w", encoding="utf-8") as f:
            f.write(json.dumps({"filename": filename, "text": text, "uploaded": uploaded}))
    except Exception:
        pass


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

    if name.endswith((".heic", ".heif")):
        # iPhone photos are often HEIC; Pillow can't read them without pyheif.
        try:
            import pyheif
            from PIL import Image
            img = Image.open(io.BytesIO(pyheif.read(raw).data))
            img.load()
        except Exception:
            return "", ("This screenshot is a HEIC file (iPhone format) I can't read. "
                        "On your phone, convert it to JPG/PNG (open it, tap Share > Save as image, "
                        "or screenshot it again) and upload that. Or just paste the job text.")
    else:
        # Anything Pillow can open -> OCR it (covers png/jpg/jpeg/webp/bmp/gif/tif/tiff).
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:
            return "", f"unsupported or unreadable image: {name}"

    if not ocr_available():
        return "", ("Tesseract OCR is not installed yet, so I cannot read text out of "
                    "screenshots. Paste the job text into the box instead.")
    import pytesseract
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) < 1400:                      # upscale small phone shots
        f = 1400 / max(img.size)
        img = img.resize((int(img.width * f), int(img.height * f)))
    try:
        txt = pytesseract.image_to_string(img)
    except Exception as e:
        return "", f"screenshot OCR failed ({type(e).__name__}). Paste the job text instead."
    return txt, "screenshot via OCR"

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


def build_verdict(rep, resume_text):
    """Plain-English synthesis: what this JD means for THIS candidate specifically."""
    gaps = rep["gaps"]
    near = [g for g in gaps if g.get("near")]
    true_gaps = [g for g in gaps if not g.get("near")]
    have = rep["have"]
    role = rep["role_label"]

    lines = []
    if have:
        top = ", ".join(h["key"].split(" (")[0] for h in have[:3])
        lines.append(f"Your strongest cards for this {role} role are already on your resume: {top}.")
    if near:
        n = ", ".join(g["key"].split(" (")[0] for g in near[:4])
        lines.append(f"You're closer than the JD makes it look on: {n}. "
                     f"You already do the work - you just lack the framework name or the tool. "
                     f"That's a vocabulary-and-proof gap, not a from-zero gap, and it's the fastest to close.")
    if true_gaps:
        t = ", ".join(g["key"].split(" (")[0] for g in true_gaps[:4])
        lines.append(f"Genuine new skills to learn: {t}. "
                     f"These need real practice, not just words - use your own NHT programme as the lab.")
    if not gaps and have:
        lines.append("Nothing in this JD is missing from your background. Lead with proof and numbers in the interview.")
    if not have and gaps:
        lines.append("This role is a stretch from your current resume - build the adjacent proof first.")
    bottom = (f"Bottom line: {len(near)} skill(s) you can reframe from what you already do, "
              f"{len(true_gaps)} you need to actually learn. "
              f"Start with the 'near' ones - they turn into interview wins in days, not months.")
    lines.append(bottom)
    return " ".join(lines)


# --------------------------------------------------------------- routes
def analyse(jd_text, resume_text):
    jd, res = norm(jd_text), norm(resume_text)
    role = detect_role(jd_text)

    asked, gaps, have = [], [], []
    for s in SKILLS:
        in_jd = any(hits(a, jd) for a in s["aliases"]) or hits(s["key"], jd)
        if not in_jd:
            continue
        in_res = any(hits(a, res) for a in s["aliases"]) or hits(s["key"], res)
        # ADJACENT: resume shows related work even if it doesn't name the framework
        adj = s.get("adjacent", [])
        adj_hit = [a for a in adj if hits(a, res)]
        rec = dict(key=s["key"], why=s["why"], learn=s["learn"], proof=s["proof"],
                   link=s.get("link", ""), tools=s.get("tools", []),
                   matched=[a for a in s["aliases"] if hits(a, jd)][:6],
                   bridge=adj_hit[:3])
        asked.append(rec)
        if in_res:
            have.append(rec)
        else:
            # mark as 'near' if resume shows adjacent work -> reframe as vocabulary gap
            rec["near"] = bool(adj_hit)
            gaps.append(rec)

    # role-critical skills the JD implies even if not literally worded
    implied = []
    for s in SKILLS:
        if role in s["roles"] and s["key"] not in [a["key"] for a in asked]:
            if not (any(hits(a, res) for a in s["aliases"]) or hits(s["key"], res)):
                adj = s.get("adjacent", [])
                adj_hit = [a for a in adj if hits(a, res)]
                implied.append(dict(key=s["key"], why=s["why"], learn=s["learn"],
                                    proof=s["proof"], link=s.get("link", ""), tools=s.get("tools", []),
                                    matched=[], near=bool(adj_hit), bridge=adj_hit[:3]))

    asked_count = len(asked)
    # Honest match %: only meaningful when the JD actually shares our skill
    # vocabulary. If it matched NONE of the 21 known skills, the JD is from a
    # different field entirely -> do NOT show a flattering number.
    if asked_count == 0:
        match_pct = 0
        out_of_domain = True
    else:
        match_pct = round(100 * len(have) / asked_count)
        # never claim a confident match on a single weak signal
        if asked_count < 3 and match_pct >= 100:
            match_pct = 0
        out_of_domain = False
    total = asked_count or 1
    rep = dict(role=role, role_label=ROLE_LABELS[role],
               match_pct=match_pct,
               have=have, gaps=gaps, implied=implied[:6],
               asked_count=asked_count, out_of_domain=out_of_domain,
               generated=dt.datetime.now().strftime("%d %b %Y, %I:%M %p"))
    rep["verdict"] = build_verdict(rep, res)
    return rep


def clearance_plan(rep, jd_text, resume_text):
    """Build the point-by-point 'Interview Clearance Plan' the user asked for:
    Job / Experience required / Skills lacking in resume / Skills required /
    How to learn / Chances if learned / Resources (links, books, YouTube, free tools).
    This is more structured than the gap cards and reads like a checklist."""
    res = norm(resume_text)
    jd = norm(jd_text)
    role_label = rep["role_label"]

    # Roll up every skill the JD asks for but the resume is weak on (gaps + implied)
    items = []
    for g in (rep["gaps"] + rep["implied"]):
        # "how to learn" = the learn steps (already free-first)
        learn = g.get("learn", [])
        # "resources" = the link (official) + tools block (books/yt/tools/ai)
        tools = g.get("tools", [])
        # split tools into labelled buckets for clean pointers
        books, yt, free_tools, ai = [], [], [], []
        for t in tools:
            tl = t.lower()
            if "youtube" in tl:
                yt.append(t)
            elif "book" in tl or "read" in tl or "article" in tl or "atd" in tl \
                 or "kirkpatrick" in tl or "shrm" in tl or "iso" in tl or "nngroup" in tl:
                books.append(t)
            elif "ai:" in tl or "chatgpt" in tl or "gemini" in tl or "copilot" in tl:
                ai.append(t)
            else:
                free_tools.append(t)
        link = (g.get("link") or "").strip()
        items.append(dict(
            key=g["key"],
            why=g.get("why", ""),
            proof=g.get("proof", ""),
            near=bool(g.get("near")),
            bridge=g.get("bridge", []),
            learn=learn,
            link=link,
            books=books, yt=yt, free_tools=free_tools, ai=ai))
    return dict(role_label=role_label, role_read=ROLE_LABELS.get(rep["role"], role_label),
                asked=len(rep["have"]) + len(rep["gaps"]),
                have=len(rep["have"]), gaps=len(rep["gaps"]),
                items=items)


ROLE_EXPERIENCE = {
    "training manager": "Typically 4-7 yrs in training/L&D with team coordination; shows you can own a curriculum end-to-end and manage people, not just deliver sessions.",
    "l&d": "Typically 3-6 yrs in L&D / OD; shows you design learning strategy and run programmes, not only facilitate.",
    "trainer": "Typically 1-4 yrs facilitating; shows you can deliver and design sessions and handle a demo round cold.",
    "rsm": "Typically 4-8 yrs with a carried revenue/quota number and team/territory handling; L&D-to-RSM is a stretch without quota history.",
}
def interview_questions(rep, jd_text, resume_text):
    """Generate the questions THIS employer will most likely ask this candidate,
    drawn from the gaps the engine found plus the candidate's own resume.

    This is the part a plain ChatGPT paste can't do well: it knows the gaps and
    your real background, so every question comes with a tailored 'your answer'
    built from your resume and the bridge skill you already have."""
    res = norm(resume_text)

    def answer_for(rec):
        # Prefer the candidate's own resume wording that shows adjacent work.
        if rec.get("bridge"):
            return (f"Use the {rec['key']} angle: on your resume you already do "
                    f"'{rec['bridge'][0]}'. Reframe it as {rec['key']} — same work, "
                    f"framework name + one proof point. {rec.get('proof', '')}")
        if rec.get("near"):
            return (f"You're close on {rec['key']} — you do the work but may not name "
                    f"the framework. Say: '{rec.get('proof', '')}'")
        return (f"Be honest you're building it, and pivot to the closest thing you do: "
                f"{rec.get('proof', '')}")

    items = []
    for g in (rep["gaps"] + rep["implied"])[:6]:
        items.append(dict(
            q=f"The JD asks for {g['key']}. How do you handle that today, and where's your proof?",
            tip=g.get("why", ""),
            a=answer_for(g)))
    # A couple of role-level behavioural questions.
    items.append(dict(
        q=f"Why are you moving from training operations to a {rep['role_label']} role?",
        tip="They'll probe motivation. Tie it to ownership you already have.",
        a="Point to owning the 12-trainer team, the NHT/Technical curriculum and MIS "
          "governance — you already operate at manager scope, you want the title and the "
          "strategic remit that comes with it."))
    items.append(dict(
        q=f"What's your biggest gap for a {rep['role_label']} role, and how are you closing it?",
        tip="They reward self-awareness here, not 'I have no weaknesses'.",
        a="Name the top true gap from above, show the concrete learning step you've started "
          "(one of the 'how to learn it' links), and a date you'll have proof."))
    return items


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
                           ocr=ocr_available(),
                           ai=__import__("llm").ai_status())


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
    set_resume(f.filename, text, dt.datetime.now().strftime("%d %b %Y, %I:%M %p"))
    flash(f"Resume saved ({note}, {len(text.split())} words). It stays saved - no need to upload again.", "good")
    return redirect(url_for("home"))


@app.route("/analyse", methods=["POST"])
def do_analyse():
    # ---- Accept the resume INLINE so the whole thing is one submit. ----
    # This is the real fix for the "it keeps asking me to upload my resume"
    # loop: you attach the resume together with the JD and press one button.
    # Two input styles supported:
    #   * multipart file upload (local / fast networks)
    #   * base64 JSON (phone on Render free, where multipart uploads time out)
    import base64 as _b64

    def _from_b64(field):
        raw = request.form.get(field) or (request.json.get(field) if request.is_json else "")
        if not raw:
            return None
        try:
            data = _b64.b64decode(str(raw).split(",", 1)[-1])
        except Exception:
            return None
        name = (request.form.get(field + "_name") or "upload.bin").lower()
        class _F:
            filename = name
            def read(self): return data
        return _F()

    rf = request.files.get("resume_inline")
    if not (rf and rf.filename):
        rf = _from_b64("resume_b64")
    if rf and getattr(rf, "filename", None):
        text, note = extract_text(rf)
        if len(text.strip()) >= 50:
            set_resume(getattr(rf, "filename", "resume"), text,
                       dt.datetime.now().strftime("%d %b %Y, %I:%M %p"))

    r = get_resume()
    if not r:
        flash("Attach your resume (or use Step 1 to save it once) and try again.", "bad")
        return redirect(url_for("home"))

    chunks, notes = [], []
    for f in request.files.getlist("shots"):
        if f and f.filename:
            t, n = extract_text(f)
            chunks.append(t)
            notes.append(f"{f.filename}: {n}")
    # base64 screenshots (phone / Render free where multipart upload times out)
    import base64 as _b64
    for i in range(1, 12):
        raw = request.form.get(f"shot_b64_{i}")
        if not raw:
            continue
        try:
            data = _b64.b64decode(str(raw).split(",", 1)[-1])
        except Exception:
            continue
        class _F:
            filename = "screenshot.jpg"
            def read(self): return data
        t, n = extract_text(_F())
        if t.strip():
            chunks.append(t)
            notes.append(f"screenshot_{i}: {n}")
    pasted = (request.form.get("pasted") or "").strip()
    if pasted:
        chunks.append(pasted)
        notes.append("pasted text")

    jd_text = "\n\n".join(x for x in chunks if x).strip()
    if len(jd_text) < 60:
        flash("I couldn't read enough job-description text from what you gave me. "
              + (" | ".join(notes) if notes else "Upload a screenshot or paste the text.")
              + " Tip: if it's a phone screenshot, make sure it's JPG/PNG (not HEIC), and the text is clear. "
              + "You can also just paste the job text.", "bad")
        return redirect(url_for("home"))

    # Domain-aware engine selection (your preference, 2026-08-17):
    #   * Training / L&D / RSM / Trainer JDs  -> the built-in rule-based engine.
    #     This is the ADDIE / Kirkpatrick / TNA / TNI + resources read you liked,
    #     and we do NOT run AI on these (AI only made them thinner).
    #   * Cross-field JDs (e.g. Database Developer) where the 21-skill matcher
    #     finds NOTHING -> escalate to the AI rigorous path for an accurate read.
    #     If no key / rate-limited, honestly fall back to rule-based.
    rb = analyse(jd_text, r["text"])
    if not rb.get("out_of_domain"):
        rep = rb
        rep["sources"] = notes
        rep["ai_mode"] = False
        rep["interview"] = interview_questions(rep, jd_text, r["text"])
        rep["plan"] = clearance_plan(rep, jd_text, r["text"])
    else:
        ai_res = _llm.analyse_with_error(jd_text, r["text"])
        if ai_res.get("ok"):
            rep = ai_res["rep"]
            rep["sources"] = notes
            rep["ai_mode"] = True
            rep["plan"] = llm_clearance(rep)
            if not rep.get("interview"):
                rep["interview"] = interview_questions(rep, jd_text, r["text"])
        else:
            rep = rb
            rep["sources"] = notes
            rep["ai_mode"] = False
            rep["ai_error"] = ai_res.get("ai_error")
            rep["interview"] = interview_questions(rep, jd_text, r["text"])
            rep["plan"] = clearance_plan(rep, jd_text, r["text"])
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
    return render_template("report.html", jd=row, r=json.loads(row["report"]),
                           ROLE_EXPERIENCE=ROLE_EXPERIENCE)


@app.route("/paste")
def paste_page():
    """Zero-API AI path, step 1: build the full prompt (resume + JD + Tejas's
    exact method) so it can be copied into any free AI (ChatGPT etc)."""
    import pasteback
    r = get_resume()
    if not r:
        flash("Save your resume first (Step 1 on the home page), then come back here.", "bad")
        return redirect(url_for("home"))
    return render_template("paste.html", resume=r, prompt=pasteback.build_prompt(r["text"], ""))


@app.route("/paste-back", methods=["POST"])
def paste_back():
    """Zero-API AI path, step 2: glue the AI's reply back into a full report.
    Needs a saved resume. The JD is whatever was in the prompt you copied — we
    take it from the hidden field so the stored report has the real JD text."""
    import pasteback
    r = get_resume()
    if not r:
        flash("Save your resume first (Step 1 on the home page), then come back.", "bad")
        return redirect(url_for("home"))
    jd_text = (request.form.get("jd_text") or "").strip()
    reply = (request.form.get("reply") or "").strip()
    title = (request.form.get("title") or "").strip()
    if len(jd_text) < 60:
        flash("Paste the job description text in the 'Job description' box before pasting the AI reply.", "bad")
        return redirect(url_for("paste_page"))
    rb = analyse(jd_text, r["text"])
    if not rb.get("out_of_domain"):
        # Training / L&D / RSM / Trainer JD -> keep the liked rule-based read,
        # the pasted-AI path is only for out-of-domain jobs.
        flash("This looks like a training/L&D role — the app's built-in analysis "
              "(ADDIE, Kirkpatrick, TNA, TNI + resources) is already the right fit, "
              "so the paste-back step was skipped. Just use the normal 'Find my skill gap'.", "good")
        # still save the rule-based read so it appears in history
        rep = rb
        rep["sources"] = ["rule-based (training role)"]
        rep["ai_mode"] = False
        rep["ai_engine"] = "rule-based (training)"
    else:
        if len(reply) < 40:
            flash("Paste the AI's full reply (the JSON answer) in the box.", "bad")
            return redirect(url_for("paste_page"))
        rep, err = pasteback.from_paste(r["text"], jd_text, reply)
        if err:
            flash(err, "bad")
            return redirect(url_for("paste_page"))
    # reuse the live-AI saving path so it shows in history with the same shape
    title = title or (jd_text.strip().splitlines()[0][:70] if jd_text.strip() else "Pasted AI analysis")
    with db() as c:
        cur = c.execute("INSERT INTO jd(title,role,source,jd_text,report,created) VALUES(?,?,?,?,?,?)",
                        (title, rep["role"], "paste-back (no API key)", jd_text, json.dumps(rep),
                         dt.datetime.now().strftime("%d %b %Y, %I:%M %p")))
        new_id = cur.lastrowid
    return redirect(url_for("report", jd_id=new_id))


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
    return render_template("phone.html", ip=lan_ip(), port=5055,
                           cloud_url="https://career-coach-fnyw.onrender.com")


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
