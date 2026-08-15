"""Optional AI analysis layer.

Supports TWO providers so the user is never stuck:
  - Google Gemini  : needs env GEMINI_API_KEY  (free tier at aistudio.google.com/apikey)
  - OpenAI          : needs env OPENAI_API_KEY  (free tier at platform.openai.com/api-keys)

If a key for either is present, the app uses that provider for a REAL expert
skill-gap analysis (reads the actual JD + resume, returns genuine gaps, role
read, match %, interview Q&A, learning plan). If NEITHER key is set, or the call
fails, analyse() returns None and the app falls back to the built-in rule-based
engine in app.py — so the app never breaks.

Uses only the standard library (urllib) — no extra pip install needed.

ai_status() returns a plain dict the UI can show, so the user can SEE why AI is
or isn't working (no key / which provider / rejected by the API).
"""
import os
import json
import urllib.request
import urllib.error
import datetime as _dt

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash").strip() or "gemini-1.5-flash"
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def provider():
    """Return 'gemini', 'openai', or None in priority order."""
    if GEMINI_KEY:
        return "gemini"
    if OPENAI_KEY:
        return "openai"
    return None


def ai_enabled():
    """True if any supported key is configured at runtime."""
    return provider() is not None


_PROMPT = """You are a world-class career-skills analyst and interview coach for Indian job markets (Naukri, LinkedIn). A candidate pasted their RESUME and a JOB DESCRIPTION. Do a precise, expert skill-gap analysis.

RULES:
- Identify the REAL skills the job needs — not generic buzzwords. Think like a hiring manager + L&D expert. Cover: domain skills, frameworks/models (e.g. ADDIE, Kirkpatrick, OKRs, KPIs, competency models), tools (Excel, Power BI, LMS, CRM, ATS), behaviours (stakeholder management, influencing, coaching), and industry knowledge.
- For each gap, judge whether the candidate ALREADY does adjacent work on their resume (so it's a vocabulary/proof gap, not from-zero) or it's a genuine new skill.
- Be specific to THIS resume and THIS JD. Do not recycle the same fixed list for every job.
- Chinese-whisper-free: only claim a gap if it is genuinely in the JD or strongly implied by the role.
- ALSO explain WHY the candidate's profile does not match: is it lack of experience, missing skills, or a totally different department/domain? If there is some overlap, say what specific skills would take them to a strong match.

RESUME:
\"\"\"{resume_text}\"\"\"

JOB DESCRIPTION:
\"\"\"{jd_text}\"\"\"

Return ONLY valid JSON in this exact shape:
{{
  "role_label": "exact job title/role this JD is for, short",
  "match_pct": <integer 0-100: how much of what the JD asks for is already on the resume>,
  "have": [ {{"key":"skill the resume clearly shows","why":"why it matters for this JD"}} ],
  "gaps": [
    {{
      "key":"missing/weak skill the JD needs",
      "near": <true if resume shows adjacent work, false if genuine new skill>,
      "why":"why the JD wants it",
      "proof":"one line the candidate can say in interview to show they have/are close to it",
      "learn":["free-first step 1","step 2","step 3"],
      "link":"one official/credible URL to start learning (or empty string)",
      "books":"recommended book or authoritative article name (or empty string)",
      "youtube":"a YouTube search/topic that teaches it (or empty string)",
      "free_tool":"a free tool to practise it (or empty string)",
      "chances":"Short verdict: 'High' if near/reframe, 'Medium' if new but learnable, 'Low' if a big stretch"
    }}
  ],
  "interview": [
    {{"q":"a specific question THIS employer will ask given these gaps","a":"a tailored answer built from the candidate's own resume + the bridge skill"}}
  ],
  "verdict":"2-3 sentence plain-English summary of what this JD means for THIS candidate, including why the profile does/doesn't match (experience vs skills vs different department)"
}}

Output strictly the JSON. No markdown, no commentary."""


def _gemini_call(prompt):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.4, "maxOutputTokens": 8192},
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _openai_call(prompt):
    url = "https://api.openai.com/v1/chat/completions"
    body = json.dumps({
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {OPENAI_KEY}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def _call(prompt):
    """Call whichever provider has a key. Return model text or None on failure."""
    which = provider()
    if which == "gemini":
        try:
            return _gemini_call(prompt)
        except Exception:
            return None
    if which == "openai":
        try:
            return _openai_call(prompt)
        except Exception:
            return None
    return None


def ai_status():
    """Diagnostic the UI can show. Returns dict:
       {enabled, provider, key_present, model, error}
    error is a human string when a key is set but the API rejected it.
    """
    which = provider()
    if not which:
        return {"enabled": False, "provider": None, "key_present": False,
                "model": "", "error": "No API key set. Add GEMINI_API_KEY or OPENAI_API_KEY in Render."}
    # probe with a tiny call to see if the key is accepted
    try:
        if which == "gemini":
            _gemini_call("Reply with the single word: ok")
        else:
            _openai_call("Reply with the single word: ok")
        return {"enabled": True, "provider": which,
                "key_present": True,
                "model": GEMINI_MODEL if which == "gemini" else OPENAI_MODEL,
                "error": ""}
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:200] if e.fp else str(e)
        return {"enabled": False, "provider": which, "key_present": True,
                "model": GEMINI_MODEL if which == "gemini" else OPENAI_MODEL,
                "error": f"API rejected the key ({e.code}): {msg}"}
    except Exception as e:
        return {"enabled": False, "provider": which, "key_present": True,
                "model": GEMINI_MODEL if which == "gemini" else OPENAI_MODEL,
                "error": f"Call failed: {type(e).__name__}: {str(e)[:160]}"}


def analyse(jd_text, resume_text):
    """AI analysis. Returns a dict shaped like app.analyse()'s rep, or None."""
    if not provider():
        return None

    prompt = _PROMPT.format(resume_text=resume_text, jd_text=jd_text)
    raw = _call(prompt)
    if not raw:
        return None
    # strip ```json fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw)
    except Exception:
        return None

    # Normalise into the shape app.py / templates expect.
    gaps = data.get("gaps", []) or []
    for g in gaps:
        g.setdefault("near", False)
        g.setdefault("why", "")
        g.setdefault("proof", "")
        g.setdefault("learn", [])
        g.setdefault("link", g.get("link", "") or "")
        g.setdefault("books", g.get("books", "") or "")
        g.setdefault("youtube", g.get("youtube", "") or "")
        g.setdefault("free_tool", g.get("free_tool", "") or "")
        g.setdefault("chances", "")
        g.setdefault("bridge", [])
        g.setdefault("tools", [t for t in [g.get("books"), g.get("youtube"), g.get("free_tool")] if t])
    have = data.get("have", []) or []
    for h in have:
        h.setdefault("why", "")
    interview = []
    for it in data.get("interview", []) or []:
        interview.append({"q": it.get("q", ""), "a": it.get("a", ""), "tip": ""})

    rep = dict(
        role=data.get("role_label", "Role"),
        role_label=data.get("role_label", "Role"),
        match_pct=int(data.get("match_pct", 0) or 0),
        have=have,
        gaps=gaps,
        implied=[],
        asked_count=len(have) + len(gaps),
        generated=_dt.datetime.now().strftime("%d %b %Y, %I:%M %p"),
        verdict=data.get("verdict", ""),
        interview=interview,
        ai=True,
    )
    return rep


def clearance_plan_from_ai(rep):
    """Build the clearance-plan items from the AI rep (same shape as app.clearance_plan)."""
    items = []
    for g in rep.get("gaps", []):
        items.append(dict(
            key=g.get("key", ""),
            why=g.get("why", ""),
            proof=g.get("proof", ""),
            near=bool(g.get("near")),
            bridge=g.get("bridge", []),
            learn=g.get("learn", []),
            link=(g.get("link") or "").strip(),
            books=[b for b in [g.get("books", "")] if b],
            yt=[y for y in [g.get("youtube", "")] if y],
            free_tools=[t for t in [g.get("free_tool", "")] if t],
            ai=[],
        ))
    return dict(role_label=rep.get("role_label", ""),
                role_read=rep.get("role_label", ""),
                asked=rep.get("asked_count", 0),
                have=len(rep.get("have", [])),
                gaps=len(rep.get("gaps", [])),
                items=items)
