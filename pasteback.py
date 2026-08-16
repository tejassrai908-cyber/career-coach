"""Zero-API AI path: build Tejas's exact-method prompt, then glue a pasted
ChatGPT/any-AI reply back into the SAME report the live OpenRouter path produces.

Why this exists: Tejas rejected the generic analysis and wanted his rigorous
method applied automatically, but without an API key the app server can't call
an AI by itself. This module lets the app (a) write the full prompt + both
inputs so he can copy it to his free ChatGPT, and (b) take the AI's reply back,
repair it, and feed it through llm.normalise_ai_data() so the report renders
identically to the live AI path. No key, works on the cloud link.
"""

import json

# Tejas's exact method, hard-wired. Kept in lock-step with llm._PROMPT.
PROMPT_INTRO = """You are a meticulous, evidence-based career analyst and interview coach for Indian job markets (Naukri, LinkedIn). A candidate pasted their RESUME and a full JOB DESCRIPTION. Produce a rigorous, audit-able skill-gap analysis that follows the candidate's own method EXACTLY.

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
{{
  "role_label": "exact job title/role this JD is for, short",
  "match_pct": <integer 0-100: how much of what the JD EXPLICITLY asks for is already on the resume>,
  "have": [ {{"key":"skill explicitly on the resume","why":"why it matters for this JD","jd_quote":"exact JD wording that matches it, or empty string"}} ],
  "gaps": [
    {{
      "key":"requirement extracted from the JD",
      "category":"explicitly_required | preferred | implied",
      "jd_quote":"VERBATIM phrase from the JD that states this requirement (or '' if implied)",
      "on_resume": <true iftd resume shows this skill, false if missing>,
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
    }}
  ],
  "exp_diff":"plain-English experience difference: years you have vs years wanted, your domain/function vs the JD's domain/function, level vs function gap",
  "dept_diff":"plain-English department difference: the JD's department/function vs your current department/function",
  "required_skills":["core skill 1 from the JD","core skill 2","core skill 3"],
  "qualification":{{"jd_wants":"degree/certification the JD states (or 'not specified')","resume_has":"what the resume shows for education/certs (or 'not specified')","gap":"what is missing from the resume vs the JD","learn":["how to close the qualification gap"]}},
  "interview": [
    {{"q":"a specific question THIS employer will ask given the gaps","a":"a tailored answer built from the candidate's own resume + the bridge skill"}}
  ],
  "verdict":"2-3 sentence plain-English summary: what this JD needs, how the resume matches, and whether the gap is experience vs skills vs a different department. Say 'not specified' for any missing JD field."
}}

If you cannot output strict JSON, that's fine — write plain text with clear headings and bullet points using exactly these section titles: "Skills you are missing", "Skills you already have", "Experience difference", "Qualification difference", "How to learn these skills" (with book / YouTube / tool / course / link lines and URLs), and "Overall verdict". The app reads both formats.

Output strictly the JSON when possible. No commentary outside the answer."""


def build_prompt(resume_text, jd_text):
    """Return the full prompt (resume + JD + method) ready to copy into ChatGPT."""
    resume_text = (resume_text or "").strip()
    jd_text = (jd_text or "").strip()
    return PROMPT_INTRO.format(resume_text=resume_text, jd_text=jd_text)


def _coerce(data):
    """Best-effort repair of a pasted AI reply into the strict contract.

    Handles: a fenced ```json block, prose wrapping, Python-style True/False,
    and missing optional fields. Returns a dict (maybe partial) or None.
    """
    if isinstance(data, dict):
        raw = data
    else:
        s = (data or "").strip()
        if s.startswith("```"):
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start:end + 1]
        # Python booleans -> JSON booleans so json.loads doesn't choke
        s = s.replace("True", "true").replace("False", "false")
        try:
            raw = json.loads(s)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None

    # normalise list fields that the model sometimes omits
    raw.setdefault("have", [])
    raw.setdefault("gaps", [])
    raw.setdefault("interview", [])
    for g in raw["gaps"]:
        if not isinstance(g, dict):
            continue
        g.setdefault("category", "explicitly_required")
        g.setdefault("jd_quote", "")
        g.setdefault("on_resume", False)
        g.setdefault("near", False)
        g.setdefault("learn", [])
        for k in ("why", "proof", "link", "books", "youtube", "free_tool", "chances", "key"):
            g.setdefault(k, "")
    return raw


import re

# Headings ChatGPT commonly writes (plain text, not JSON). Used to split a
# prose reply into labelled sections so we can still build the report.
_SECTION_HINTS = [
    ("skills_missing", ("skill", "gap", "missing", "required", "what you need", "to learn", "not have", "lacking")),
    ("skills_have", ("strength", "already", "have", "current", "possess", "match")),
    ("exp_diff", ("experience", "exp ", "years")),
    ("dept_diff", ("department", "function", "domain")),
    ("qualification", ("qualif", "education", "degree", "certif", "academic")),
    ("how_to_learn", ("how to", "learn", "resource", "close", "bridge", "develop", "improve", "course", "study")),
    ("verdict", ("verdict", "summary", "recommend", "overall", "conclusion")),
]


def _sectionize(text):
    """Split prose into (title, body) chunks using markdown/labeled headings."""
    lines = text.splitlines()
    chunks = []
    cur_title = None
    cur_body = []
    heading_re = re.compile(r'^\s*(#{1,6}\s+(.+?)|\*\*(.+?)\*\*|(.+?):)\s*$')
    for ln in lines:
        m = heading_re.match(ln)
        title = None
        if m:
            title = (m.group(2) or m.group(3) or m.group(4) or "").strip()
        # also treat a short Title-Case / ALLCAPS line as a heading
        if not title and 3 <= len(ln.strip()) <= 55 and ln.strip().istitle():
            title = ln.strip()
        if title:
            if cur_title is not None or cur_body:
                chunks.append((cur_title or "intro", "\n".join(cur_body).strip()))
            cur_title = title
            cur_body = []
        else:
            cur_body.append(ln)
    if cur_title is not None or cur_body:
        chunks.append((cur_title or "intro", "\n".join(cur_body).strip()))
    return chunks


def _classify(title):
    t = (title or "").lower()
    for key, hints in _SECTION_HINTS:
        if any(h in t for h in hints):
            return key
    return None


def _bullets(body):
    out = []
    for ln in body.splitlines():
        s = ln.strip().lstrip("-*•▪◦‣·").strip()
        if not s:
            continue
        if s.lower().startswith("todo"):
            continue
        out.append(s)
    return out


def _urls(body):
    return re.findall(r'https?://[^\s)\]\">]+', body)


def _qual_field(body, *keys):
    for ln in body.splitlines():
        low = ln.lower()
        for k in keys:
            if low.startswith(k + ":") or (k in low and ":" in ln and low.index(k) < 12):
                return ln.split(":", 1)[1].strip()
    return ""


def from_prose(reply):
    """Best-effort parse of a FREE-TEXT ChatGPT reply into the report shape.

    Used when the reply isn't valid JSON (free ChatGPT often writes prose with
    headings). Heuristic but robust: split by headings, map to fields, extract
    resources (URLs + labelled Book/YouTube/Tool lines)."""
    chunks = _sectionize(reply)
    # first unclassified chunk often holds the role + match %
    raw = {"match_pct": 0, "have": [], "gaps": [], "required_skills": [],
           "exp_diff": "", "dept_diff": "", "qualification": {},
           "interview": [], "verdict": ""}
    pct = re.search(r'(\d{1,3})\s*%', reply)
    if pct:
        raw["match_pct"] = max(0, min(100, int(pct.group(1))))
    # role label: first heading that isn't generic, else first line
    for title, _ in chunks:
        if title and title.lower() not in ("intro", "summary", "analysis"):
            raw["role_label"] = title[:60]
            break
    full_resources = []
    how_to = []
    for title, body in chunks:
        kind = _classify(title)
        if kind == "skills_missing":
            for b in _bullets(body):
                raw["gaps"].append({"key": b, "category": "explicitly_required",
                                    "on_resume": False, "near": False, "why": "",
                                    "proof": "", "learn": [], "link": "",
                                    "books": "", "youtube": "", "free_tool": "",
                                    "more": [], "chances": "", "jd_quote": ""})
                raw["required_skills"].append(b)
        elif kind == "skills_have":
            for b in _bullets(body):
                raw["have"].append({"key": b, "why": "", "jd_quote": ""})
        elif kind == "exp_diff":
            raw["exp_diff"] = body
        elif kind == "dept_diff":
            raw["dept_diff"] = body
        elif kind == "qualification":
            q = {"jd_wants": _qual_field(body, "jd wants", "jd asks", "required education", "education required"),
                 "resume_has": _qual_field(body, "resume", "you have", "candidate has"),
                 "gap": _qual_field(body, "gap", "missing", "difference"),
                 "learn": []}
            if not q["jd_wants"] and not q["gap"]:
                q["gap"] = body  # whole section is the comparison text
            learn = _bullets(body)
            q["learn"] = [l for l in learn if not l.startswith(("jd wants", "resume", "gap"))]
            raw["qualification"] = q
        elif kind == "how_to_learn":
            how_to.extend(_bullets(body))
            full_resources.extend(_urls(body))
            # labelled resource lines
            for ln in body.splitlines():
                low = ln.lower()
                if any(t in low for t in ("book", "youtube", "tool", "course", "link", "certif")):
                    full_resources.append(ln.strip())
        elif kind == "verdict":
            raw["verdict"] = body
    # distribute shared resources / how-to across every missing skill
    for g in raw["gaps"]:
        g["learn"] = how_to[:5] if how_to else g["learn"]
        g["more"] = full_resources[:4]
        if full_resources:
            g["link"] = g["link"] or full_resources[0]
    if not raw.get("role_label"):
        raw["role_label"] = (reply.strip().splitlines()[0][:60] if reply.strip() else "Analysis")
    return raw


def from_paste(resume_text, jd_text, reply):
    """Glue a pasted AI reply into a normalised report dict (same shape as the
    live OpenRouter path). Returns (report_dict, error_string_or_None).

    Accepts BOTH a strict JSON answer AND free-form prose from ChatGPT: if the
    reply isn't parseable JSON, it falls back to from_prose() which splits the
    text by the headings ChatGPT naturally writes.
    """
    import llm  # local import keeps startup clean
    raw = _coerce(reply)
    if raw is None:
        # Free ChatGPT often answers in prose instead of JSON -> parse that.
        raw = from_prose(reply)
    if not raw or (not raw.get("gaps") and not raw.get("have")
                   and not raw.get("exp_diff") and not raw.get("qualification")):
        return None, ("Couldn't read the analysis. Paste ChatGPT's FULL reply "
                      "(the headings + bullet points it wrote). If it gave JSON, "
                      "include the { and }.")
    rep = llm.normalise_ai_data(raw)
    if rep is None:
        return None, "The reply didn't contain a readable analysis. Paste ChatGPT's full answer."
    rep["ai_mode"] = True
    rep["ai_engine"] = "paste-back (ChatGPT / any AI via copy-paste, no API key)"
    rep["sources"] = ["paste-back from ChatGPT/any AI"]
    return rep, None
