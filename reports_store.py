"""GitHub-backed durable store for Career Coach reports + resume.

WHY THIS EXISTS
---------------
Render's free plan wipes the local disk (career.db, resume_mirror.txt) on every
restart/redeploy, so past reports vanished. This module mirrors each saved
report and the resume into the app's OWN GitHub repo (data/ folder) via the
GitHub Contents API. On a cold boot (DB empty) the app re-seeds from GitHub, so
your past reports survive any number of restarts.

It is fully OPTIONAL and FAIL-SAFE:
- If GH_TOKEN / GH_REPO are not set, every function is a no-op (old behaviour).
- If GitHub is down / token wrong / rate-limited, we log and return False; the
  app keeps working on the local (ephemeral) DB as before. Nothing throws.

No third-party deps: uses only the Python standard library (urllib).
"""

import base64
import json
import os
import time
import urllib.request
import urllib.error

API = "https://api.github.com"

# Owner/repo. Defaults to Tejas's repo; override with GH_REPO="owner/repo".
REPO = os.environ.get("GH_REPO", "tejassrai908-cyber/career-coach").strip()
TOKEN = os.environ.get("GH_TOKEN", "").strip()

# Floor: never let automatic cleanup drop below this many reports.
KEEP_AT_LEAST = 3

DATA_DIR = "data"  # folder inside the repo where we stash JSON


def enabled():
    """True only when a token + repo are configured. Safe to call anywhere."""
    return bool(TOKEN and REPO)


def _headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "career-coach",
    }


def _url(path):
    return f"{API}/repos/{REPO}/contents/{path}"


def _request(method, path, body=None, _tries=2):
    """One GitHub API call. Returns parsed JSON or raises on hard failure.

    Retries once on transient network errors. Returns None (not raise) for
    expected 404s so callers can treat 'not found' gracefully.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_url(path), data=data, method=method,
                                 headers=_headers())
    last_err = None
    for _ in range(_tries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # not found -> caller decides
            last_err = f"HTTP {e.code}"
            if e.code in (401, 403):  # auth/permission: don't retry
                break
        except Exception as e:  # network blip
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(1)
    raise RuntimeError(f"GitHub {method} {path} failed: {last_err}")


def _put(path, content_str, message, sha=None):
    """Create or update a file. Returns the new file sha (str) or raises."""
    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    res = _request("PUT", path, payload)
    return res.get("content", {}).get("sha") if res else None


def _get(path):
    """Return (decoded_text, sha) for a file, or (None, None) if missing."""
    res = _request("GET", path)
    if not res:
        return None, None
    try:
        text = base64.b64decode(res["content"]).decode("utf-8", "ignore")
    except Exception:
        return None, None
    return text, res.get("sha")


def _delete(path, sha, message):
    _request("DELETE", path, {"message": message, "sha": sha})


# ----------------------------------------------------------- reports

def save_report(rid, title, role, report_dict, jd_text, created):
    """Persist one report to GitHub. Returns True on success, False if disabled
    or it failed (never raises)."""
    if not enabled():
        return False
    try:
        doc = {
            "id": rid,
            "title": title,
            "role": role,
            "created": created,
            "jd_text": jd_text,
            "report": report_dict,
        }
        path = f"{DATA_DIR}/report-{rid}.json"
        # If the file already exists (re-save), supply its sha for the update.
        _, sha = _get(path)
        _put(path, json.dumps(doc, indent=1, ensure_ascii=False),
             f"career-coach: save report #{rid} ({title[:40]})", sha=sha)
        return True
    except Exception as e:
        print(f"[reports_store] save_report #{rid} failed: {e}")
        return False


def delete_report(rid):
    """Remove a report from GitHub (best-effort)."""
    if not enabled():
        return
    try:
        path = f"{DATA_DIR}/report-{rid}.json"
        text, sha = _get(path)
        if sha:
            _delete(path, sha, f"career-coach: delete report #{rid}")
    except Exception as e:
        print(f"[reports_store] delete_report #{rid} failed: {e}")


def list_report_ids():
    """Return list of report ids present in the repo data/ folder (newest last)."""
    if not enabled():
        return []
    try:
        res = _request("GET", DATA_DIR)
        if not res:
            return []
        ids = []
        for item in res:
            name = item.get("name", "")
            if name.startswith("report-") and name.endswith(".json"):
                try:
                    ids.append(int(name[len("report-"):-len(".json")]))
                except ValueError:
                    pass
        return sorted(ids)
    except Exception as e:
        print(f"[reports_store] list_report_ids failed: {e}")
        return []


def max_report_id():
    """Highest report id stored in GitHub (0 if none / disabled)."""
    ids = list_report_ids()
    return max(ids) if ids else 0


def load_all_reports():
    """Fetch every stored report. Returns list of dicts with keys:
    id, title, role, created, jd_text, report. Empty if disabled/failed."""
    out = []
    if not enabled():
        return out
    for rid in list_report_ids():
        try:
            text, _ = _get(f"{DATA_DIR}/report-{rid}.json")
            if not text:
                continue
            doc = json.loads(text)
            out.append({
                "id": doc.get("id", rid),
                "title": doc.get("title", "Pasted ChatGPT analysis"),
                "role": doc.get("role", ""),
                "created": doc.get("created", ""),
                "jd_text": doc.get("jd_text", "") or "",
                "report": doc.get("report", {}),
            })
        except Exception as e:
            print(f"[reports_store] load report #{rid} failed: {e}")
    return out


# ----------------------------------------------------------- resume

def save_resume(name, filename, text, uploaded):
    if not enabled():
        return False
    try:
        doc = {"name": name, "filename": filename,
               "text": text, "uploaded": uploaded}
        path = f"{DATA_DIR}/resume.json"
        _, sha = _get(path)
        _put(path, json.dumps(doc, indent=1, ensure_ascii=False),
             "career-coach: save resume", sha=sha)
        return True
    except Exception as e:
        print(f"[reports_store] save_resume failed: {e}")
        return False


def load_resume():
    if not enabled():
        return None
    try:
        text, _ = _get(f"{DATA_DIR}/resume.json")
        return json.loads(text) if text else None
    except Exception as e:
        print(f"[reports_store] load_resume failed: {e}")
        return None
