import os, uuid, csv, datetime, re, textwrap
import google.generativeai as genai
from urllib.parse import quote_plus
from flask import Flask, request, render_template, redirect, url_for, make_response, send_from_directory
from bs4 import BeautifulSoup

# ===============================
# 写死 API Key （仅限本地实验用）
# ===============================
genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
WEBPAGES_DIR = os.path.join(APP_ROOT, "webpages")
LOGS_DIR = os.path.join(APP_ROOT, "logs")

# ----- Railway environment configuration -----
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "gour")
# Prefer a mounted volume in Railway for persistence (configure LOGS_DIR=/app/logs)
try:
    LOGS_DIR = os.environ.get("LOGS_DIR", LOGS_DIR)  # if LOGS_DIR defined earlier keep it, else use env
except NameError:
    LOGS_DIR = os.environ.get("LOGS_DIR", os.path.join(APP_ROOT, "logs"))
os.makedirs(LOGS_DIR, exist_ok=True)

SUBMISSION_LOG = os.path.join(LOGS_DIR, "submissions.csv")
EVENTS_LOG = os.path.join(LOGS_DIR, "events.csv")

ADMIN_PASSWORD = "gour"

app = Flask(__name__)

# ------------------------------
# Session & Logging
# ------------------------------
def get_or_set_session_id(resp=None):
    sid = request.cookies.get("session_id")
    if not sid:
        sid = str(uuid.uuid4())
        if resp is None:
            resp = make_response()
        resp.set_cookie("session_id", sid, httponly=True, samesite="Lax")
    return sid, resp


# ------------ Prolific ID helper ------------
def get_prolific_id():
    pid = request.cookies.get("prolific_id", "").strip()
    return pid or ""


def check_admin():
    return request.cookies.get("admin_access") == "1"

def log_event(prolific_id, etype, query="", target=""):
    now = datetime.datetime.utcnow().isoformat()
    with open(EVENTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([now, prolific_id, etype, query, target])

# ------------------------------
# Data Loading
# ------------------------------
def load_local_pages():
    pages = []
    if not os.path.exists(WEBPAGES_DIR):
        return pages
    for name in os.listdir(WEBPAGES_DIR):
        path = os.path.join(WEBPAGES_DIR, name)
        if os.path.isfile(path) and name.lower().endswith((".html",".htm",".txt",".md")):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    raw = f.read()
                title = name
                text = raw
                href = f"/local/{name}"
                if name.lower().endswith((".html",".htm")):
                    soup = BeautifulSoup(raw, "lxml")
                    if soup.title and soup.title.text.strip():
                        title = soup.title.text.strip()
                    text = soup.get_text(" ", strip=True)
                else:
                    first_line = raw.strip().splitlines()[0] if raw.strip().splitlines() else name
                    if len(first_line) < 120:
                        title = first_line.strip()
                pages.append({"name":name, "title":title, "text":text, "href":href})
            except Exception:
                continue
    return pages

def score_query(text, q):
    words = [w.lower() for w in re.findall(r"\b\w+\b", q) if len(w) > 2]
    if not words:
        return 0
    score = 0
    lt = text.lower()
    for w in words:
        score += lt.count(w)
    return score

# ------------------------------
# Gemini Overview
# ------------------------------
def _truncate(s, limit=1600):
    s = re.sub(r"\s+", " ", s or "").strip()
    return (s[:limit] + "…") if len(s) > limit else s

def make_overview_gemini(query, pages, max_sources=8, model_name="gemini-1.5-flash"):
    ranked = sorted(pages, key=lambda p: score_query(p.get("text",""), query), reverse=True)[:max_sources]

    numbered, citations = [], []
    for i, p in enumerate(ranked, start=1):
        title = p.get("title") or p.get("name") or f"Source {i}"
        text = p.get("text") or ""
        href = p.get("href") or p.get("name") or ""
        numbered.append(f"[{i}] {title}\n{textwrap.dedent(_truncate(text, 3000))}")
        citations.append({"idx": i, "title": title, "href": href})

    system_rules = textwrap.dedent("""
    You are generating a concise 'AI Overview' for a search results page.
    Use ONLY the provided sources. Do not invent facts or use outside knowledge.
    Write 3–6 sentences max. For each factual sentence, append inline citation(s)
    like [1] or [2][5]. Avoid markdown headings, bullet lists, disclaimers.
    End with the overview only.
    """)

    sources_blob = "\n\n".join(numbered)
    prompt = f"QUERY:\n{query}\n\nSOURCES:\n{sources_blob}\n\nINSTRUCTIONS:\n{system_rules}\n"

    model = genai.GenerativeModel(model_name)
    out = model.generate_content(prompt)
    text = (out.text or "").strip()
    if not re.search(r"\[\d+\]", text):
        text += " [1]"
    return text, citations

def get_overview(query, pages):
    try:
        return make_overview_gemini(query, pages)
    except Exception as e:
        print("Gemini failed, fallback:", e)
        return "Error generating AI Overview", []

# ------------------------------
# Routes
# ------------------------------
@app.route("/")

def index():
    # gate: if prolific_id not set, show gate first
    if not get_prolific_id():
        return render_template("prolific_gate.html", title="Enter Prolific ID")
    resp = make_response(render_template("index.html", title="Research Search"))
    _sid, resp = get_or_set_session_id(resp)
    return resp

@app.route("/results")
def results():
    query = request.args.get("q","").strip()
    pages = load_local_pages()
    overview, cites = get_overview(query, pages)
    resp = make_response(render_template("results.html", title="Results", overview=overview, citations=cites, query=query))
    sid, resp = get_or_set_session_id(resp)
    log_event(sid, 'search', query=query, target=str([ (c.get('title') or '') + '|' + (c.get('href') or '') for c in cites ]))
    return resp

@app.route("/submit", methods=["GET","POST"])
def submit():
    if request.method == "GET":
        query = request.args.get("q","")
        resp = make_response(render_template("submit.html", title="Submit", query=query))
        _sid, resp = get_or_set_session_id(resp)
        return resp
    query = request.form.get("q","")
    text = request.form.get("conclusion","").strip()
    word_count = len(re.findall(r"\b\w+\b", text))
    if word_count < 100:
        return render_template("submit.html", title="Submit", query=query, error="Please write at least 100 words."), 400
    sid = get_prolific_id() or request.cookies.get("session_id") or str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat()
    with open(SUBMISSION_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([now, sid, query, word_count, text])
    log_event(get_prolific_id() or sid, 'submit', query=query, target=str(word_count))
    return redirect(url_for("thanks"))

@app.route("/thanks")
def thanks():
    resp = make_response(render_template("thanks.html", title="Thanks"))
    _sid, resp = get_or_set_session_id(resp)
    return resp

@app.route("/local/<path:filename>")
def serve_local(filename):
    return send_from_directory(WEBPAGES_DIR, filename, as_attachment=False)


# ------------------------------
# Admin: simple password-gated pages
# ------------------------------
from flask import send_file

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    # Simple inline password gate; sets a short-lived cookie
    next_url = request.values.get("next") or "/admin/logs"
    if request.method == "POST":
        pw = request.form.get("password","").strip()
        if pw == ADMIN_PASSWORD:
            resp = make_response(redirect(next_url))
            # mark access cookie for 1 day
            resp.set_cookie("admin_access", "1", max_age=24*3600, httponly=True, samesite="Lax")
            return resp
        return render_template("admin_login.html", title="Admin Login", error="Wrong password.", next=next_url), 401
    # GET
    if check_admin():
        return redirect(next_url)
    return render_template("admin_login.html", title="Admin Login", next=next_url)

@app.route("/admin/logout")
def admin_logout():
    resp = make_response(redirect("/"))
    resp.delete_cookie("admin_access")
    return resp

def _ensure_csv(path: str, header: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) or os.stat(path).st_size == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)

def _read_csv_rows(path: str):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for r in reader:
            rows.append(r)
    return rows

@app.route("/admin/logs")
def admin_logs():
    # allow direct access with ?pwd=
    pw = request.args.get("pwd")
    if pw == ADMIN_PASSWORD:
        resp = make_response(redirect(url_for("admin_logs")))
        resp.set_cookie("admin_access", "1", max_age=24*3600, httponly=True, samesite="Lax")
        return resp
    if not check_admin():
        return redirect(url_for("admin_login", next="/admin/logs"))
    _ensure_csv(SUBMISSION_LOG, ["timestamp","prolific_id","query","word_count","text"])
    rows = _read_csv_rows(SUBMISSION_LOG)
    return render_template("admin_logs.html", title="Submissions Log", rows=rows)

@app.route("/admin/events")
def admin_events():
    pw = request.args.get("pwd")
    if pw == ADMIN_PASSWORD:
        resp = make_response(redirect(url_for("admin_events")))
        resp.set_cookie("admin_access", "1", max_age=24*3600, httponly=True, samesite="Lax")
        return resp
    if not check_admin():
        return redirect(url_for("admin_login", next="/admin/events"))
    _ensure_csv(EVENTS_LOG, ["timestamp","prolific_id","type","query","target"])
    rows = _read_csv_rows(EVENTS_LOG)
    return render_template("admin_events.html", title="Events Log", rows=rows)

@app.route("/admin/logs/download")
def admin_logs_download():
    if not check_admin():
        return redirect(url_for("admin_login", next="/admin/logs"))
    _ensure_csv(SUBMISSION_LOG, ["timestamp","prolific_id","query","word_count","text"])
    return send_file(SUBMISSION_LOG, as_attachment=True, download_name="submissions.csv")

@app.route("/admin/events/download")
def admin_events_download():
    if not check_admin():
        return redirect(url_for("admin_login", next="/admin/events"))
    _ensure_csv(EVENTS_LOG, ["timestamp","prolific_id","type","query","target"])
    return send_file(EVENTS_LOG, as_attachment=True, download_name="events.csv")

@app.route("/admin/logs/clear", methods=["POST"])
def admin_logs_clear():
    if not check_admin():
        return redirect(url_for("admin_login", next="/admin/logs"))
    _ensure_csv(SUBMISSION_LOG, ["timestamp","prolific_id","query","word_count","text"])
    # overwrite with header only
    with open(SUBMISSION_LOG, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["timestamp","session_id","query","word_count","text"])
    return redirect(url_for("admin_logs"))

@app.route("/admin/events/clear", methods=["POST"])
def admin_events_clear():
    if not check_admin():
        return redirect(url_for("admin_login", next="/admin/events"))
    _ensure_csv(EVENTS_LOG, ["timestamp","prolific_id","type","query","target"])
    with open(EVENTS_LOG, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["timestamp","session_id","type","query","target"])
    return redirect(url_for("admin_events"))


@app.route("/set_prolific", methods=["POST"])
def set_prolific():
    pid = (request.form.get("prolific_id","") or "").strip()
    if not pid:
        return render_template("prolific_gate.html", title="Enter Prolific ID", error="Prolific ID is required.")
    resp = make_response(redirect(url_for("index")))
    # Persist prolific_id for 30 days
    resp.set_cookie("prolific_id", pid, max_age=30*24*3600, httponly=False, samesite="Lax")
    # optional: keep session cookie for compatibility
    _sid, resp = get_or_set_session_id(resp)
    # Log
    _ensure_csv(EVENTS_LOG, ["timestamp","prolific_id","type","query","target"])
    log_event(pid, "prolific_set", target=pid)
    return resp


@app.route("/api/overview", methods=["POST"])
def api_overview():
    if not get_prolific_id():
        return {"error":"prolific_id_required"}, 400
    data = request.get_json(silent=True) or {}
    query = (data.get("q","") or "").strip()
    pages = load_local_pages()
    overview, cites = get_overview(query, pages)
    # log search
    log_event(get_prolific_id(), 'search', query=query, target=str([ (c.get('title') or '') + '|' + (c.get('href') or '') for c in cites ]))
    # return renderable fragments
    html_overview = render_template("_overview_fragment.html", overview=overview)
    html_citations = render_template("_citations_fragment.html", citations=cites, query=query)
    return {"overview_html": html_overview, "citations_html": html_citations}

# ------------------------------
# Run
# ------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
