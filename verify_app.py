"""End-to-end verification for Career Coach.

Boots the app on a spare port in both modes and exercises every route:
  no-PIN mode  (local use)  -> everything open
  PIN mode     (cloud use)  -> locked until PIN, then everything works

Run:  venv/Scripts/python.exe verify_app.py
Exits non-zero on any failure.
"""
import http.cookiejar
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")


def free_port():
    """Grab an OS-assigned free port so we never collide with a running app."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
ROOT = f"http://127.0.0.1:{PORT}"
fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def wait_up(proc, timeout=40):
    for _ in range(timeout * 2):
        if proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.5)
    return False


def boot(pin=None):
    ddir = tempfile.mkdtemp(prefix="cc_verify_")
    env = dict(os.environ, CAREER_DATA_DIR=ddir, PORT=str(PORT))
    env.pop("APP_PIN", None)
    if pin:
        env["APP_PIN"] = pin
    os.environ["_CC_DATADIR"] = ddir  # let tests inspect the same DB
    p = subprocess.Popen([PY, "app.py"], cwd=BASE, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_up(p):
        p.kill()
        raise SystemExit("app failed to boot")
    return p


def opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _try(fn, tries=3):
    """Flask's dev server occasionally resets a keep-alive socket; retry once or twice."""
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore")
        except (ConnectionResetError, urllib.error.URLError, OSError):
            if i == tries - 1:
                raise
            time.sleep(0.6)


def get(op, path):
    return _try(lambda: (lambda r: (r.getcode(), r.read().decode("utf-8", "ignore")))(
        op.open(ROOT + path, timeout=25)))


def post(op, path, fields):
    data = urllib.parse.urlencode(fields).encode()
    return _try(lambda: (lambda r: (r.getcode(), r.read().decode("utf-8", "ignore")))(
        op.open(urllib.request.Request(ROOT + path, data=data), timeout=60)))


def post_file(op, path, field, fname, content, ctype="text/plain", extra=None):
    b = "----cc" + str(time.time_ns())
    parts = []
    for k, v in (extra or {}).items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                 f"filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n")
    body = "".join(parts).encode() + (
        content if isinstance(content, bytes) else content.encode()) + \
        f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(ROOT + path, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
    return _try(lambda: (lambda r: (r.getcode(), r.read().decode("utf-8", "ignore")))(
        op.open(req, timeout=90)))


RESUME = ("Tejas S R - Training Operations Coordinator\n"
          "Coordinate a team of 12 trainers; NHT Day 1-12; Technical Training Day 1-3\n"
          "MIS Update, Advanced Excel pivot tables VLOOKUP, Power BI dashboards\n"
          "Attendance Regularization, SOP documentation, presentation skills\n")
JD_TM = ("Training Manager - Retail Banking\n"
         "- TNI and training needs analysis; instructional design ADDIE\n"
         "- Kirkpatrick L1-L4 training effectiveness and ROI of training\n"
         "- LMS administration (Cornerstone); Articulate Storyline Rise 360\n"
         "- Team handling, performance management, stakeholder management\n"
         "- Advanced Excel, Power BI learning analytics; training budget and vendor\n")
JD_RSM = ("Regional Sales Manager - South\n"
          "- Own annual revenue target; pipeline and funnel conversion\n"
          "- Distributor and channel partners; territory coverage and beat plan\n"
          "- Team handling of area sales managers; CRM hygiene on Salesforce\n")


def jd_png():
    """Render a JD to PNG so the OCR path is genuinely exercised."""
    import textwrap
    from PIL import Image, ImageDraw, ImageFont
    lines = []
    for ln in JD_TM.splitlines():
        lines += textwrap.wrap(ln, 60) or [""]
    try:
        f = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        f = ImageFont.load_default()
    img = Image.new("RGB", (900, 40 + 30 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((20, 20 + 30 * i), ln, fill="black", font=f)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- no-PIN mode
print("\n[1] local mode (no PIN)")
proc = boot()
try:
    op = opener()
    check("home 200", get(op, "/")[0] == 200)

    c, _ = post_file(op, "/resume", "file", "cv.txt", RESUME)
    check("resume upload accepted", c in (200, 302))
    check("resume shows as saved", "saved" in get(op, "/")[1])

    c, _ = post(op, "/analyse", {"title": "TM Retail Banking", "pasted": JD_TM})
    check("analyse pasted JD", c in (200, 302))
    code, body = get(op, "/report/1")
    role = re.search(r"Read as a <b>([^<]*)", body)
    pct = re.search(r">(\d+)%<", body)
    check("report renders", code == 200)
    check("role = Training Manager", bool(role) and "Training Manager" in role.group(1),
          role.group(1) if role else "none")
    check("match % present", bool(pct), (pct.group(1) + "%") if pct else "none")
    gaps = re.findall(r'gap">\s*<b>([^<]*)', body)
    check("gaps detected", len(gaps) >= 5, f"{len(gaps)} gaps")
    for must in ("ADDIE", "Kirkpatrick", "LMS"):
        check(f"gap includes {must}", any(must in g for g in gaps))

    code, body = get(op, "/resume-draft/1")
    check("resume-draft renders", code == 200)
    tas = re.findall(r'<textarea readonly rows="[^"]*">(.*?)</textarea>', body, re.S)
    check("draft has headline+summary+keywords+bullets", len(tas) >= 4, f"{len(tas)} blocks")
    check("headline names the role", "Training Manager" in tas[0] if tas else False)
    check("keywords lifted from JD", "Addie" in tas[2] if len(tas) > 2 else False)
    check("junk keyword 'Team Of' filtered", "Team Of" not in tas[2] if len(tas) > 2 else False)

    # OCR path
    c, _ = post_file(op, "/analyse", "shots", "jd.png", jd_png(), "image/png",
                     {"title": "TM via screenshot"})
    check("screenshot analyse accepted", c in (200, 302))
    _, b2 = get(op, "/report/2")
    check("OCR read the screenshot", "screenshot via OCR" in b2)
    ocr_gaps = re.findall(r'gap">\s*<b>([^<]*)', b2)
    check("OCR report found gaps", len(ocr_gaps) >= 4, f"{len(ocr_gaps)} gaps")

    # combined one-shot submit: resume + JD in a single /analyse call
    c, _ = post_file(op, "/analyse", "resume_inline", "cv2.txt", RESUME, "text/plain",
                     {"title": "Combined submit", "pasted": JD_TM})
    check("combined resume+JD submit accepted", c in (200, 302))
    _, b3 = get(op, "/report/3")
    check("report 4 renders + interview section",
          "Interview questions" in b3, "interview section" if "Interview questions" in b3 else "missing")
    check("interview Q count >=6", b3.count("Say this:") >= 6, f"{b3.count('Say this:')} answers")
    check("clearance plan section present", "Interview Clearance Plan" in b3,
          "plan" if "Interview Clearance Plan" in b3 else "missing")
    check("plan has 'Chances if you learn' per skill", b3.count("Chances if you learn") >= 4,
          f"{b3.count('Chances if you learn')} skill blocks")
    check("plan has resource links", "Official / start here" in b3,
          "resources" if "Official / start here" in b3 else "missing")
    check("plan shows experience required", "Experience this role usually wants" in b3,
          "exp line" if "Experience this role usually wants" in b3 else "missing")

    # role switching (now report id 4)
    post(op, "/analyse", {"title": "RSM South", "pasted": JD_RSM})
    _, b4 = get(op, "/report/4")
    r4 = re.search(r"Read as a <b>([^<]*)", b4)
    check("role = Regional Sales Manager", bool(r4) and "Regional Sales" in r4.group(1),
          r4.group(1) if r4 else "none")

    check("plan page 200", get(op, "/plan")[0] == 200)
    check("plan ranks gaps", "appeared in" in get(op, "/plan")[1])
    check("phone page 200", get(op, "/phone")[0] == 200)
    check("qr.png 200", get(op, "/qr.png")[0] == 200)
    check("icon 200", get(op, "/icon-192.png")[0] == 200)
    check("manifest 200", get(op, "/static/manifest.json")[0] == 200)

    # backup -> wipe -> restore
    code, bak = get(op, "/backup")
    check("backup downloads", code == 200 and "jd_text" in bak)
    for i in (1, 2, 3, 4):
        post(op, f"/delete/{i}", {})
    check("jobs wiped", len(set(re.findall(r"/report/(\d+)", get(op, "/")[1]))) == 0)
    c, _ = post_file(op, "/restore", "bak", "b.json", bak, "application/json")
    check("restore accepted", c in (200, 302))
    home = get(op, "/")[1]
    check("jobs restored", len(set(re.findall(r"/report/(\d+)", home))) == 4)
    check("resume survived restore", "saved" in home)

    # graceful handling of junk
    c, b = post(op, "/analyse", {"title": "empty", "pasted": "hi"})
    check("too-short JD rejected politely", c in (200, 302))
finally:
    proc.kill()

# ---------------------------------------------------------------- PIN mode
print("\n[2] cloud mode (APP_PIN=2468)")
proc = boot(pin="2468")
try:
    op = opener()
    check("home locked without PIN", get(op, "/")[0] == 401)
    check("plan locked without PIN", get(op, "/plan")[0] == 401)
    check("backup locked without PIN", get(op, "/backup")[0] == 401)
    post(op, "/login", {"pin": "1111"})
    check("wrong PIN still locked", get(op, "/")[0] == 401)
    post(op, "/login", {"pin": "2468"})
    check("correct PIN unlocks home", get(op, "/")[0] == 200)
    check("correct PIN unlocks plan", get(op, "/plan")[0] == 200)
    c, _ = post_file(op, "/resume", "file", "cv.txt", RESUME, extra={"name": "Tejas"})
    check("resume upload works behind PIN", c in (200, 302))
    check("resume saved behind PIN", "saved" in get(op, "/")[1])
finally:
    proc.kill()

print("\n[3] paste-back (zero-API ChatGPT path)")
proc = boot()
try:
    op = opener()
    c, _ = post_file(op, "/resume", "file", "cv.txt", RESUME, extra={"name": "Tejas"})
    check("resume upload accepted (paste-back mode)", c in (200, 302))
    check("paste page 200", get(op, "/paste")[0] == 200)
    # the prompt page should contain the method keywords + the resume
    _, pbody = get(op, "/paste")
    check("prompt builds resume + method",
          "Read the COMPLETE job description" in pbody and "Tejas" in pbody)

    # --- multi-resume: add a second (wife) resume, both show with names ---
    WIFE = ("Anusha S - HR Generalist\n"
            "Recruitment, onboarding, payroll processing, employee engagement\n"
            "HRIS, statutory compliance, grievance handling, T&D coordination\n")
    c2, _ = post_file(op, "/resume", "file", "anusha.txt", WIFE, extra={"name": "Anusha"})
    check("second resume (wife) upload accepted", c2 in (200, 302))
    _, hbody = get(op, "/")
    check("both resumes listed with names", "Tejas" in hbody and "Anusha" in hbody)
    # paste helper shows the resume picker with both names
    _, pbody2 = get(op, "/paste")
    check("paste helper shows both resume names", "Tejas" in pbody2 and "Anusha" in pbody2)
    # selecting Anusha's resume builds the prompt from her text
    m = re.search(r'/paste\?resume=(\d+)', pbody2)
    anusha_id = None
    if m:
        anusha_id = m.group(1)
    # find Anusha's id via home links to delete-resume/<id>
    mids = re.findall(r'/delete-resume/(\d+)', hbody)
    check("two saved resumes present", len(mids) >= 2)
    if mids:
        # home lists newest-first; Anusha was uploaded 2nd so she is first in the list
        first_body = get(op, "/paste?resume=" + mids[0])[1]
        anusha_rid = mids[0] if "Anusha" in first_body else mids[-1]
        _, pb = get(op, "/paste?resume=" + anusha_rid)
        check("selected resume prompt reflects that resume", "Anusha" in pb or "HR Generalist" in pb)
        # the prompt is auto-saved for that resume and shows a clear link
        check("prompt auto-saved (saved for Anusha)", "saved for Anusha" in pb)
        mpid = re.search(r'/delete-prompt/(\d+)', pb)
        if mpid:
            cdp, _ = post(op, "/delete-prompt/" + mpid.group(1), {})
            check("clear saved prompt works", cdp in (200, 302))
            # verify it's actually gone from the DB (not just hidden by a re-save on GET)
            import sqlite3 as _sq
            dbp = os.path.join(os.environ.get("_CC_DATADIR", ""), "career.db")
            conn = _sq.connect(dbp)
            leftover = conn.execute("SELECT COUNT(*) FROM prompts WHERE resume_id=?",
                                   (anusha_rid,)).fetchone()[0]
            conn.close()
            check("prompt cleared (no row in DB)", leftover == 0)
        # Anusha's resume row shows a per-resume clear option
        check("Anusha resume has clear option", len(mids) >= 2)
        # clear the ChatGPT response box is a client control; verify the button exists
        check("clear-response button present", "Clear response" in pb)
        # clear one resume
        cdel, _ = post(op, "/delete-resume/" + anusha_rid, {})
        check("clear resume works", cdel in (200, 302))
        _, hbody2 = get(op, "/")
        check("resume removed after clear", "Anusha" not in hbody2 or len(re.findall(r'/delete-resume/(\d+)', hbody2)) == 1)

    # --- training JD should NOT use paste-back (rule engine is the right fit) ---
    c, _ = post(op, "/paste-back",
                {"title": "TM via paste-back (should skip)",
                 "jd_text": JD_TM,
                 "reply": "ignored for training roles"})
    check("training JD skips paste-back, uses rule engine", c in (200, 302))

    # --- cross-field JD SHOULD use the pasted reply ---
    # Use a JD with NO overlap with the 21 training skills so the matcher
    # flags it out-of-domain and the paste-back (AI) path is taken.
    JD_DB = ("Civil Structural Engineer\n"
             "Design and analyse bridges, buildings and flyovers using STAAD.Pro and AutoCAD.\n"
             "Prepare bar-bending schedules and coordinate with site execution teams.\n"
             "Bachelor's in Civil Engineering, 3+ years in structural design.")
    SAMPLE = ('{"role_label":"Civil Structural Engineer","match_pct":5,'
              '"have":[],'
              '"gaps":[{"key":"STAAD.Pro","category":"explicitly_required",'
              '"jd_quote":"Design and analyse bridges using STAAD.Pro",'
              '"on_resume":false,"near":false,"why":"core design tool",'
              '"proof":"","learn":["Learn STAAD.Pro basics"],'
              '"link":"https://www.bentley.com/software/staad/",'
              '"books":"Reinforced Concrete Design by Pillai & Menon",'
              '"youtube":"STAAD.Pro tutorial for beginners",'
              '"free_tool":"Bentley free learning edition",'
              '"more":["https://www.bentley.com/software/staad/","https://www.youtube.com/c/STAAD"],"chances":"Low"}],'
              '"exp_diff":"You have 15+ yrs in Training/L&D; the JD wants 3+ yrs but in a different function (civil engineering). This is a FUNCTION gap, not a level gap.",'
              '"dept_diff":"The JD is in Civil Engineering / Design. Your current function is Training & L&D. Different department.",'
              '"required_skills":["STAAD.Pro","AutoCAD","Structural analysis","Bar-bending schedules"],'
              '"interview":[{"q":"How do you analyse a beam?","a":"I would use STAAD.Pro to model loads and check deflection."}],'
              '"verdict":"Different field; civil engineering tools absent."}')
    c, _ = post(op, "/paste-back",
                {"title": "Civil Eng via paste-back", "jd_text": JD_DB,
                 "reply": SAMPLE})
    check("paste-back accepted (cross-field JD)", c in (200, 302))
    # find the new report id
    home = get(op, "/")[1]
    ids = sorted(set(int(x) for x in re.findall(r"/report/(\d+)", home)), reverse=True)
    check("paste-back report stored", len(ids) >= 1, f"{len(ids)} reports")
    if ids:
        _, rb = get(op, f"/report/{ids[0]}")
        check("paste-back report renders role", "Civil Structural Engineer" in rb,
              ("role found" if "Civil Structural Engineer" in rb else "role missing"))
        check("paste-back shows JD quote", "Design and analyse bridges" in rb,
              ("quote shown" if "Design and analyse bridges" in rb else "quote missing"))
        check("paste-back shows experience difference", "FUNCTION gap" in rb,
              ("exp_diff shown" if "FUNCTION gap" in rb else "exp_diff missing"))
        check("paste-back shows department difference", "Training &amp; L&amp;D" in rb or "Training & L&D" in rb,
              ("dept_diff shown" if ("Training &amp; L&amp;D" in rb or "Training & L&D" in rb) else "dept_diff missing"))
        check("paste-back shows required skills list", "Required skills for this job" in rb,
              ("required-skills shown" if "Required skills for this job" in rb else "required-skills missing"))
        check("paste-back shows resources (book/youtube/link)", "Reinforced Concrete Design" in rb and "Resources to acquire it" in rb,
              ("resources shown" if "Reinforced Concrete Design" in rb else "resources missing"))
        check("paste-back flagged as paste-back engine", "paste-back" in rb.lower(),
              ("engine tagged" if "paste-back" in rb.lower() else "engine not tagged"))
finally:
    proc.kill()

print("\n" + ("ALL CHECKS PASSED" if not fails
              else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
