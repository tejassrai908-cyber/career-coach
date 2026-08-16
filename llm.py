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
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip() or os.environ.get("GROQ_KEY", "").strip() \
           or os.environ.get("AI_API_KEY", "").strip()  # AI_API_KEY = easy-to-type alias for Groq
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile"
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY", "").strip()
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar").strip() or "sonar"
HF_KEY = os.environ.get("HF_API_KEY", "").strip() or os.environ.get("HUGGINGFACE_API_KEY", "").strip()
HF_MODEL = os.environ.get("HF_MODEL", "meta-llama/Llama-3.3-70B-Instruct").strip() or "meta-llama/Llama-3.3-70B-Instruct"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_KEYS_RAW = os.environ.get("OPENROUTER_KEYS", "").strip()
# Key pool: combine the single-key var and the comma-separated pool var.
# The 50/day free limit is PER KEY, so stacking keys multiplies daily free quota.
_OPENROUTER_KEYS = []
for _k in [OPENROUTER_KEY, OPENROUTER_KEYS_RAW]:
    for _part in _k.split(","):
        _part = _part.strip()
        if _part and _part.startswith("sk-or"):
            _OPENROUTER_KEYS.append(_part)
# de-dup preserving order
OPENROUTER_KEYS = []
for _k in _OPENROUTER_KEYS:
    if _k not in OPENROUTER_KEYS:
        OPENROUTER_KEYS.append(_k)
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free").strip() or "google/gemma-4-31b-it:free"


def provider():
    """Return which provider has a key, in priority order.
    openrouter first (free tier, works in most regions), then perplexity/hf/gemini/openai/groq."""
    if OPENROUTER_KEYS:
        return "openrouter"
    if PERPLEXITY_KEY:
        return "perplexity"
    if HF_KEY:
        return "hf"
    if GEMINI_KEY:
        return "gemini"
    if OPENAI_KEY:
        return "openai"
    if GROQ_KEY:
        return "groq"
    return None


def ai_enabled():
    """True if any supported key is configured at runtime."""
    return provider() is not None


_PROMPT = """You are a meticulous, evidence-based career analyst and interview coach for Indian job markets (Naukri, LinkedIn). A candidate pasted their RESUME and a full JOB DESCRIPTION. Produce a rigorous, audit-able skill-gap analysis that follows the candidate's own method EXACTLY.

THE CANDIDATE'S REQUIRED METHOD (follow every step):
1. Read the COMPLETE job description. Do not summarise early.
2. Extract EVERY skill, qualification, tool, certification, and responsibility that is EXPLICITLY mentioned in the JD.
3. Do NOT add any skill that is not present in the JD. If it is only implied by the role, tag it category "implied" and label it clearly — never mix it with explicitly stated requirements.
4. For each extracted item, QUOTE the exact wording from the JD (copy the phrase verbatim) in the "jd_quote" field.
5. Compare those requirements against the RESUME.
6. Classify every requirement into exactly one of:
   - "explicitly_required": stated as a requirement ("must have", "required", "should have", duty phrasing).
   - "preferred": stated as "preferred", "nice to have", "good to have", "plus".
   - "not_mentioned": a skill the candidate has on the resume but the JD does NOT ask for (record in "have", not "gaps").
   - "implied": genuinely required by the role but not literally worded in the JD.
7. If the JD omits experience years, salary, notice period, or any field, write "not specified" — NEVER guess or invent a number.
8. For each gap, say whether the resume already shows adjacent work ("near": true) or it is a genuine new skill ("near": false), and give an interview-ready "proof" line built from the candidate's own resume.
9. After the gaps, give THREE overall comparison blocks: (a) "exp_diff" — the EXPERIENCE difference: years the candidate has vs years the JD wants, their current domain/function vs the JD's domain/function, and whether this is a level or a function gap. (b) "dept_diff" — the DEPARTMENT difference: what department/function the JD belongs to (e.g. Software Engineering & QA, Sales) versus the candidate's current department/function (e.g. Training & L&D), in plain English. (c) "required_skills" — a plain list of the core skills this JD requires, pulled only from the JD.
10. For every gap, fill "link" (one official learning URL), "books" (a recommended book/authoritative article), "youtube" (a concrete YouTube topic/channel that teaches it), "free_tool" (a free tool to practise), and "more" (a list of 2-4 EXTRA credible resource links — docs, courses, communities — when available). If a resource truly doesn't exist, use empty string / empty list, never invent a fake URL.
11. Add a "qualification" block that compares EDUCATION / CERTIFICATIONS / DEGREE the JD asks for versus what the RESUME shows. Use this exact shape:
   "qualification": {{ "jd_wants": "verbatim: the degree/certification/education the JD states (or 'not specified' if the JD does not mention education)", "resume_has": "what the resume actually shows for education/certifications (or 'not specified' if not on the resume)", "gap": "what is MISSING from the resume versus the JD's requirement", "learn": ["how to close this qualification gap — e.g. a course, certification, or credential to pursue", "..."] }}

RESUME:
\"\"\"{resume_text}\"\"\"

JOB DESCRIPTION:
\"\"\"{jd_text}\"\"\"

Return ONLY valid JSON in this exact shape:
{
  "role_label": "exact job title/role this JD is for, short",
  "match_pct": <integer 0-100: how much of what the JD EXPLICITLY asks for is already on the resume>,
  "have": [ {"key":"skill explicitly on the resume","why":"why it matters for this JD","jd_quote":"exact JD wording that matches it, or empty string"} ],
  "gaps": [
    {
      "key":"requirement extracted from the JD",
      "category":"explicitly_required | preferred | implied",
      "jd_quote":"VERBATIM phrase from the JD that states this requirement (or '' if implied)",
      "on_resume": <true if the resume shows this skill, false if missing>,
      "near": <true if resume shows adjacent work, false if genuine new skill>,
      "why":"why the JD wants it",
      "proof":"one interview-ready line from the candidate's own resume showing they have/are close to it (or '' if genuinely missing)",
      "learn":["free-first step 1","step 2","step 3"],
      "link":"one official/credible URL to start learning (or empty string)",
      "books":"recommended book or authoritative article (or empty string)",
      "youtube":"a YouTube topic that teaches it (or empty string)",
      "free_tool":"a free tool to practise it (or empty string)",
      "more":["extra credible resource link 1","extra link 2"],
      "chances":"High if near/reframe, Medium if new but learnable, Low if a big stretch"
    }
  ],
  "exp_diff":"plain-English experience difference: years you have vs years wanted, your domain/function vs the JD's domain/function, level vs function gap",
  "dept_diff":"plain-English department difference: the JD's department/function vs your current department/function",
  "required_skills":["core skill 1 from the JD","core skill 2","core skill 3"],
  "qualification":{{"jd_wants":"degree/certification the JD states (or 'not specified')","resume_has":"what the resume shows for education/certs (or 'not specified')","gap":"what is missing from the resume vs the JD","learn":["how to close the qualification gap"]}},
  "interview": [
    {"q":"a specific question THIS employer will ask given the gaps","a":"a tailored answer built from the candidate's own resume + the bridge skill"}
  ],
  "verdict":"2-3 sentence plain-English summary: what this JD needs, how the resume matches, and whether the gap is experience vs skills vs a different department. Say 'not specified' for any missing JD field."
}

If you cannot output strict JSON, that's fine — write plain text with clear headings and bullet points using exactly these section titles: "Skills you are missing", "Skills you already have", "Experience difference", "Qualification difference", "How to learn these skills" (with book / YouTube / tool / course / link lines and URLs), and "Overall verdict". The app reads both formats.

Output strictly the JSON when possible. No commentary outside the answer."""


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


def _groq_call(prompt):
    # Groq is OpenAI-compatible; just a different base URL. Free tier, no card.
    url = "https://api.groq.com/openai/v1/chat/completions"
    body = json.dumps({
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {GROQ_KEY}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def _perplexity_call(prompt):
    # Perplexity is OpenAI-compatible; free tier (sonar) works in most regions.
    url = "https://api.perplexity.ai/chat/completions"
    body = json.dumps({
        "model": PERPLEXITY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {PERPLEXITY_KEY}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def _hf_call(prompt):
    # Hugging Face free Inference API (no card). Chat format on the model endpoint.
    url = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_new_tokens": 4000,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {HF_KEY}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    if isinstance(data, dict) and "generated_text" in data:
        return data["generated_text"]
    if isinstance(data, list) and data and "generated_text" in data[0]:
        return data[0]["generated_text"]
    return data["choices"][0]["message"]["content"]


# Free OpenRouter models, tried in order. If the primary is rate-limited (429)
# we automatically fall through to the next one instead of silently dropping
# to the rule-based matcher. Update this list if models get retired
# (query: curl -s https://openrouter.ai/api/v1/models | grep ':free').
OPENROUTER_FALLBACK_MODELS = [
    OPENROUTER_MODEL,                      # user-chosen default (env OPENROUTER_MODEL)
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-26b-a4b-it:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "openai/gpt-oss-20b:free",
]


def _openrouter_call(prompt):
    # OpenRouter is OpenAI-compatible; free tier works in most regions (incl. India).
    # The 50/day free limit is PER KEY, so we rotate through the key pool and,
    # within each key, through the fallback model list. A 429 on one key/model
    # moves to the next combination instead of silently dropping to rule-based.
    keys = OPENROUTER_KEYS or ([OPENROUTER_KEY] if OPENROUTER_KEY else [])
    last_err = None
    for key in keys:
        for model in OPENROUTER_FALLBACK_MODELS:
            if not model:
                continue
            try:
                return _openrouter_one(prompt, model, key)
            except urllib.error.HTTPError as e:
                last_err = e
                # 429 (quota) or 404 (retired model) = try next key/model
                if e.code in (429, 404):
                    continue
                raise
            except Exception as e:
                last_err = e
                continue
    # Every key/model combo failed — surface the last error so analyse() reports it
    if last_err:
        raise last_err
    raise RuntimeError("No OpenRouter free key/model available")


def _openrouter_one(prompt, model, key):
    url = "https://openrouter.ai/api/v1/chat/completions"
    # system message strongly constrains output so free models don't echo the
    # prompt back (which previously broke json.loads -> silent rule-based fall-back)
    messages = [
        {"role": "system",
         "content": "You are a JSON-only career analysis API. Output ONLY a single "
                    "valid JSON object and nothing else — no prose, no markdown, "
                    "no commentary. Start with { and end with }."},
        {"role": "user", "content": prompt},
    ]
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}",
                                          "HTTP-Referer": "https://career-coach-fnyw.onrender.com",
                                          "X-Title": "Career Coach"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def _extract_json(raw):
    """Pull a JSON object out of a model response that may be wrapped in prose
    or markdown fences. Returns dict or None."""
    if not raw:
        return None
    raw = raw.strip()
    # strip ```json ... ``` fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    # try direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass
    # find first { ... last } (handles 'Here is the JSON: {...}')
    try:
        start = raw.index("{")
        end = raw.rindex("}")
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


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
    if which == "groq":
        try:
            return _groq_call(prompt)
        except Exception:
            return None
    if which == "perplexity":
        try:
            return _perplexity_call(prompt)
        except Exception:
            return None
    if which == "hf":
        try:
            return _hf_call(prompt)
        except Exception:
            return None
    if which == "openrouter":
        try:
            return _openrouter_call(prompt)
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
                "model": "",
                "error": "No API key set. Add OPENROUTER_API_KEY (free tier, openrouter.ai -> Keys) in Render — works in most regions, no card for free models."}
    # probe with a tiny call to see if the key is accepted
    try:
        if which == "gemini":
            _gemini_call("Reply with the single word: ok")
        elif which == "openai":
            _openai_call("Reply with the single word: ok")
        elif which == "groq":
            _groq_call("Reply with the single word: ok")
        elif which == "perplexity":
            _perplexity_call("Reply with the single word: ok")
        elif which == "hf":
            _hf_call("Reply with the single word: ok")
        else:
            _openrouter_call("Reply with the single word: ok")
        model = (GEMINI_MODEL if which == "gemini"
                 else OPENAI_MODEL if which == "openai"
                 else PERPLEXITY_MODEL if which == "perplexity"
                 else OPENROUTER_MODEL if which == "openrouter"
                 else HF_MODEL if which == "hf" else GROQ_MODEL)
        return {"enabled": True, "provider": which,
                "key_present": True, "model": model, "error": ""}
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:200] if e.fp else str(e)
        model = (GEMINI_MODEL if which == "gemini"
                 else OPENAI_MODEL if which == "openai"
                 else PERPLEXITY_MODEL if which == "perplexity"
                 else OPENROUTER_MODEL if which == "openrouter"
                 else HF_MODEL if which == "hf" else GROQ_MODEL)
        return {"enabled": False, "provider": which, "key_present": True,
                "model": model, "error": f"API rejected the key ({e.code}): {msg}"}
    except Exception as e:
        model = (GEMINI_MODEL if which == "gemini"
                 else OPENAI_MODEL if which == "openai"
                 else PERPLEXITY_MODEL if which == "perplexity"
                 else OPENROUTER_MODEL if which == "openrouter"
                 else HF_MODEL if which == "hf" else GROQ_MODEL)
        return {"enabled": False, "provider": which, "key_present": True,
                "model": model,
                "error": f"Call failed: {type(e).__name__}: {str(e)[:160]}"}


def normalise_ai_data(data):
    """Normalise a raw model JSON dict into the shape app.py / templates expect.

    Shared by the live OpenRouter path (analyse) AND the zero-API paste-back
    path (app.py calls this with a JSON you pasted back from ChatGPT). Returns
    the report dict, or None if ``data`` is not a usable dict.
    """
    if not isinstance(data, dict):
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
        g.setdefault("more", g.get("more", []) or [])  # extra resource links
        g.setdefault("chances", "")
        g.setdefault("category", "explicitly_required")
        g.setdefault("jd_quote", g.get("jd_quote", "") or "")
        g.setdefault("on_resume", bool(g.get("on_resume", False)))
        g.setdefault("bridge", [])
        g.setdefault("tools", [t for t in [g.get("books"), g.get("youtube"), g.get("free_tool")] if t])
    have = data.get("have", []) or []
    for h in have:
        h.setdefault("why", "")
        h.setdefault("jd_quote", h.get("jd_quote", "") or "")
    interview = []
    for it in data.get("interview", []) or []:
        interview.append({"q": it.get("q", ""), "a": it.get("a", ""), "tip": ""})

    # Split implied-category requirements into their own list (report renders
    # them separately), keeping only explicitly_required/preferred as "gaps".
    explicit_gaps = [g for g in gaps if g.get("category") != "implied"]
    implied = [g for g in gaps if g.get("category") == "implied"]

    # Top-level comparison blocks (filled by the paste-back / AI prompt).
    exp_diff = data.get("exp_diff", "") or ""
    dept_diff = data.get("dept_diff", "") or ""
    required_skills = [s for s in (data.get("required_skills", []) or []) if s]
    qualification = data.get("qualification") or {}

    return dict(
        role=data.get("role_label", "Role"),
        role_label=data.get("role_label", "Role"),
        match_pct=int(data.get("match_pct", 0) or 0),
        have=have,
        gaps=explicit_gaps,
        implied=implied,
        exp_diff=exp_diff,
        dept_diff=dept_diff,
        required_skills=required_skills,
        qualification=qualification,
        asked_count=len(have) + len(explicit_gaps),
        generated=_dt.datetime.now().strftime("%d %b %Y, %I:%M %p"),
        verdict=data.get("verdict", ""),
        interview=interview,
        ai=True,
    )


def analyse(jd_text, resume_text):
    """AI analysis. Returns a dict shaped like app.analyse()'s rep, or None."""
    if not provider():
        return None

    prompt = _PROMPT.format(resume_text=resume_text, jd_text=jd_text)
    raw = _call(prompt)
    if not raw:
        return None
    # tolerate prose-wrapped or markdown-fenced JSON from free models
    data = _extract_json(raw)
    return normalise_ai_data(data)


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


def analyse_with_error(jd_text, resume_text):
    """Like analyse() but never silently falls back. Returns a dict:
       {"ok": True, "rep": <normalised report>} on success, or
       {"ok": False, "ai_error": "<human reason>} when a key is set but the
       call failed (rate-limit / network / bad JSON). This lets the app show an
       HONEST message instead of the rule-based matcher pretending to be AI."""
    if not provider():
        return {"ok": False, "ai_error": "no_key"}
    try:
        rep = analyse(jd_text, resume_text)
    except Exception as e:
        return {"ok": False, "ai_error": f"{type(e).__name__}: {e}"}
    if not rep:
        # call succeeded (key valid) but produced no usable analysis
        return {"ok": False, "ai_error": "empty_or_unparseable_response"}
    return {"ok": True, "rep": rep}

