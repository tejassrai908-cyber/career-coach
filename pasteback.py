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
      "chances":"High if near/reframe, Medium if new but learnable, Low if a big stretch"
    }}
  ],
  "interview": [
    {{"q":"a specific question THIS employer will ask given the gaps","a":"a tailored answer built from the candidate's own resume + the bridge skill"}}
  ],
  "verdict":"2-3 sentence plain-English summary: what this JD needs, how the resume matches, and whether the gap is experience vs skills vs a different department. Say 'not specified' for any missing JD field."
}}

Output strictly the JSON. No markdown, no commentary."""


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


def from_paste(resume_text, jd_text, reply):
    """Glue a pasted AI reply into a normalised report dict (same shape as the
    live OpenRouter path). Returns (report_dict, error_string_or_None)."""
    import llm  # local import keeps startup clean
    raw = _coerce(reply)
    if raw is None:
        return None, "Could not find a valid JSON object in the reply. Make sure you pasted the AI's full answer (including the { and })."
    rep = llm.normalise_ai_data(raw)
    if rep is None:
        return None, "The reply didn't contain the expected analysis fields. Paste the AI's full JSON answer."
    rep["ai_mode"] = True
    rep["ai_engine"] = "paste-back (ChatGPT / any AI via copy-paste, no API key)"
    rep["sources"] = ["paste-back from ChatGPT/any AI"]
    return rep, None
