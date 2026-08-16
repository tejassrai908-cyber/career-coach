"""End-to-end verification for Career Coach (simplified single flow).

Boots the app on a spare port and exercises the real user journey:
  1. Upload resume -> home shows a copy-ready prompt (resume already inside).
  2. The prompt contains the resume text + Tejas's exact method.
  3. Paste ChatGPT's reply back (/paste-back) -> headed report:
     SKILLS / EXPERIENCE / QUALIFICATION + how-to-learn + resources.
  4. Both strict JSON and free-form prose replies are accepted.
  5. Backup / restore works.

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
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


CUR_ROOT = "http://127.0.0.1:1"  # set by boot() each time
fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        fails.append(name)


# --- tiny requests -------------------------------------------------------
def opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(op, path):
    try:
        with op.open(CUR_ROOT + path, timeout=40) as r:
            return r.getcode(), r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except (ConnectionError, OSError):
        return 0, ""


def post(op, path, data):
    req = urllib.request.Request(CUR_ROOT + path, data=urllib.parse.urlencode(data).encode())
    try:
        with op.open(req, timeout=60) as r:
            return r.getcode(), r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def post_file(op, path, field, fname, content, ctype="text/plain", extra=None):
    b = "----cc" + str(int(time.time() * 1e6))
    parts = []
    for k, v in (extra or {}).items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                 f'Content-Type: {ctype}\r\n\r\n')
    body = "".join(parts).encode() + content.encode() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(CUR_ROOT + path, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
    try:
        with op.open(req, timeout=60) as r:
            return r.getcode(), r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


# --- boot ----------------------------------------------------------------
def boot(pin=None):
    ddir = tempfile.mkdtemp(prefix="cc_verify_")
    os.environ["_CC_DATADIR"] = ddir  # so the test can open the same DB if needed
    # each boot gets its OWN free port (reusing one across kills races TIME_WAIT)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, CAREER_DATA_DIR=ddir, PORT=str(port))
    env.pop("APP_PIN", None)
    if pin:
        env["APP_PIN"] = pin
    p = subprocess.Popen([PY, "app.py"], cwd=BASE, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(120):
        s2 = socket.socket(); s2.settimeout(0.3)
        if s2.connect_ex(("127.0.0.1", port)) == 0:
            break
        time.sleep(0.4)
    global CUR_ROOT
    CUR_ROOT = f"http://127.0.0.1:{port}"
    return p


RESUME = ("Tejas S R - Training Operations Coordinator\n"
          "NHT Day 1-12, Technical Training Day 1-3, ADDIE, Kirkpatrick, LMS, TNA, TNI\n"
          "Led 12 trainers; MIS updates; attendance regularization; help-desk RM support\n")


# ---------------------------------------------------------- local mode
print("\n[1] local mode (resume -> prompt -> paste-back)")
proc = boot()
try:
    op = opener()
    check("home 200", get(op, "/")[0] == 200)
    check("home shows resume upload", "Save this resume" in get(op, "/")[1])

    # upload resume
    c, _ = post_file(op, "/resume", "file", "cv.txt", RESUME, extra={"name": "Tejas"})
    check("resume upload accepted", c in (200, 302))
    home = get(op, "/")[1]
    check("resume shows as saved", "saved" in home)

    # after upload, home shows the copy-ready prompt (resume inside)
    check("home shows copy step", "Copy this message" in home)
    check("prompt contains the method", "Read the COMPLETE job description" in home)
    check("prompt contains the resume text", "ADDIE" in home and "Tejas" in home)
    check("prompt tells where to paste the JD", "PASTE THE JOB DESCRIPTION TEXT HERE" in home)
    check("home links to paste-reply page", '/paste' in home)

    # /paste (step 3) loads and has the reply box
    pcode, pbody = get(op, "/paste")
    check("paste-reply page 200", pcode == 200)
    check("paste-reply has ChatGPT reply box", "ChatGPT" in pbody and "reply" in pbody)
    check("paste-reply has clear-response button", "Clear response" in pbody)

    # --- cross-field JD SHOULD use the pasted reply ---
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
              '"qualification":{"jd_wants":"Bachelor in Civil Engineering","resume_has":"Bachelor in Training and Development","gap":"JD wants a Civil Engineering degree; your degree is in Training and Development","learn":["Consider a civil engineering diploma or STAAD.Pro certification"]},'
              '"interview":[{"q":"How do you analyse a beam?","a":"I would use STAAD.Pro to model loads and check deflection."}],'
              '"verdict":"Different field; civil engineering tools absent."}')
    c, _ = post(op, "/paste-back",
                {"title": "Civil Eng via paste-back", "jd_text": JD_DB, "reply": SAMPLE})
    check("paste-back accepted (cross-field JD)", c in (200, 302))
    ids = sorted(set(int(x) for x in re.findall(r"/report/(\d+)", get(op, "/")[1])), reverse=True)
    check("paste-back report stored", len(ids) >= 1, f"{len(ids)} reports")
    if ids:
        _, rb = get(op, f"/report/{ids[0]}")
        check("report renders role", "Civil Structural Engineer" in rb)
        check("report shows experience difference", "FUNCTION gap" in rb)
        check("report shows required skills list", "Required skills for this job" in rb)
        check("report shows resources", "Reinforced Concrete Design" in rb and "Resources to learn from" in rb)
        check("report highlights skills as chips", "chip gap" in rb and "STAAD.Pro" in rb)
        check("report flags skill as 'to learn'", "to learn" in rb)
        check("report has SKILLS heading", "1 &mdash; SKILLS" in rb or "1 — SKILLS" in rb)
        check("report has EXPERIENCE heading", "2 &mdash; EXPERIENCE" in rb or "2 — EXPERIENCE" in rb)
        check("report has QUALIFICATION heading", "3 &mdash; QUALIFICATION" in rb or "3 — QUALIFICATION" in rb)
        check("report shows qualification comparison", "JD wants" in rb and "Your resume has" in rb)
        check("report flagged as paste-back engine", "paste-back" in rb.lower())

    # --- prose reply (free ChatGPT often answers in plain text, not JSON) ---
    PROSE = (
        "Civil Structural Engineer\n\n"
        "Match: 5%\n\n"
        "### Skills you are missing\n"
        "- STAAD.Pro\n"
        "- Structural analysis\n\n"
        "### Skills you already have\n"
        "- Training design\n\n"
        "### Experience difference\n"
        "You have 15+ years in Training & L&D; the JD wants 3+ years in civil engineering. This is a function gap.\n\n"
        "### Qualification difference\n"
        "JD wants: Bachelor in Civil Engineering\n"
        "Resume has: Bachelor in Training & Development\n"
        "Gap: the JD requires a civil degree; your degree is in training.\n\n"
        "### How to learn these skills\n"
        "- Book: Reinforced Concrete Design by Pillai & Menon\n"
        "- YouTube: STAAD.Pro tutorial for beginners\n"
        "- Tool: Bentley free learning edition\n"
        "- Course: https://www.bentley.com/software/staad/\n\n"
        "### Overall verdict\n"
        "Different field; civil tools are absent but learnable."
    )
    c, _ = post(op, "/paste-back", {"title": "Civil Eng prose", "jd_text": JD_DB, "reply": PROSE})
    check("prose paste-back accepted", c in (200, 302))
    ids2 = sorted(set(int(x) for x in re.findall(r"/report/(\d+)", get(op, "/")[1])), reverse=True)
    check("prose paste-back report stored", len(ids2) >= 1)
    if ids2:
        rb2 = get(op, "/report/" + str(ids2[0]))[1]
        check("prose report has SKILLS heading", "1 &mdash; SKILLS" in rb2 or "1 — SKILLS" in rb2)
        check("prose report lists a missing skill (STAAD.Pro)", "STAAD.Pro" in rb2)
        check("prose report has EXPERIENCE heading", "2 &mdash; EXPERIENCE" in rb2 or "2 — EXPERIENCE" in rb2)
        check("prose report has QUALIFICATION heading", "3 &mdash; QUALIFICATION" in rb2 or "3 — QUALIFICATION" in rb2)
        check("prose report shows a resource (book/youtube/url)",
              "Reinforced Concrete Design" in rb2 or "STAAD.Pro tutorial" in rb2 or "bentley.com" in rb2)

    # home lists past analyses
    check("home lists past analyses", "past analyses" in get(op, "/")[1].lower())

    # backup -> wipe -> restore
    code, bak = get(op, "/backup")
    check("backup downloads", code == 200 and "jd_text" in bak)
    for i in (1, 2, 3, 4):
        post(op, f"/delete/{i}", {})
    check("jobs wiped", len(set(re.findall(r"/report/(\d+)", get(op, "/")[1]))) == 0)
    c, _ = post_file(op, "/restore", "bak", "b.json", bak, "application/json")
    check("restore accepted", c in (200, 302))
    check("jobs restored", len(set(re.findall(r"/report/(\d+)", get(op, "/")[1]))) >= 1)
    check("resume survived restore", "saved" in get(op, "/")[1])
finally:
    proc.kill()


# ---------------------------------------------------------- PIN mode
print("\n[2] cloud mode (APP_PIN=2468)")
proc = boot(pin="2468")
try:
    op = opener()
    check("home locked without PIN", get(op, "/")[0] == 401)
    check("backup locked without PIN", get(op, "/backup")[0] == 401)
    check("paste locked without PIN", get(op, "/paste")[0] == 401)
    post(op, "/login", {"pin": "1111"})
    check("wrong PIN still locked", get(op, "/")[0] == 401)
    post(op, "/login", {"pin": "2468"})
    check("correct PIN unlocks home", get(op, "/")[0] == 200)
    c, _ = post_file(op, "/resume", "file", "cv.txt", RESUME, extra={"name": "Tejas"})
    check("resume upload works behind PIN", c in (200, 302))
    check("resume saved behind PIN", "saved" in get(op, "/")[1])
finally:
    proc.kill()


print("\n" + ("ALL CHECKS PASSED" if not fails
              else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
