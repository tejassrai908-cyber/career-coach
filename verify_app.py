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
    env = dict(os.environ, CAREER_DATA_DIR=tempfile.mkdtemp(prefix="cc_verify_"),
               PORT=str(PORT))
    env.pop("APP_PIN", None)
    if pin:
        env["APP_PIN"] = pin
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
    c, _ = post_file(op, "/resume", "file", "cv.txt", RESUME)
    check("resume upload works behind PIN", c in (200, 302))
    check("resume saved behind PIN", "saved" in get(op, "/")[1])
finally:
    proc.kill()

print("\n" + ("ALL CHECKS PASSED" if not fails
              else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
