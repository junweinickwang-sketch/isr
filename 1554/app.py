
import os, re, csv, glob, textwrap, datetime
from urllib.parse import urlencode, urlparse, urljoin, quote, unquote
from flask import Flask, request, render_template, redirect, url_for, make_response, send_from_directory, abort
from bs4 import BeautifulSoup
import google.generativeai as genai

# ------------------------------
# Config
# ------------------------------
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
WEBPAGES_DIR = os.path.join(APP_ROOT, "webpages")
WEBPAGES2_DIR = os.path.join(APP_ROOT, "webpages2")
LOGS_DIR = os.path.join(APP_ROOT, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

EVENTS_LOG = os.path.join(LOGS_DIR, "events.csv")
SUBMISSIONS_LOG = os.path.join(LOGS_DIR, "submissions.csv")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "gour")
GENAI_KEY = os.environ.get("GEMINI_API_KEY", "")

# Initialize Gemini (if key present)
if GENAI_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GENAI_KEY)
        _GENAI_READY = True
    except Exception:
        _GENAI_READY = False
else:
    _GENAI_READY = False

app = Flask(__name__)

# ------------------------------
# Helpers
# ------------------------------
def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _ensure_csv(path, header):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)

def _get_prolific_id():
    return request.cookies.get("prolific_id", "").strip()

def _last_digit(s: str):
    for ch in reversed(s):
        if ch.isdigit():
            return int(ch)
    return None

def choose_group_and_dir(prolific_id: str):
    """Return (group_num, directory_path). group=1 -> webpages; group=2 -> webpages2."""
    d = _last_digit(prolific_id or "")
    if d is None:
        # letters only -> default to webpages (group 1)
        return 1, WEBPAGES_DIR
    return (1, WEBPAGES_DIR) if (d % 2 == 1) else (2, WEBPAGES2_DIR)

def record_event(ev_type: str, query: str = "", target: str = "", sources=None, overview_text: str = ""):
    pid = _get_prolific_id()
    group, _ = choose_group_and_dir(pid)
    expected_header = ["timestamp","prolific_id","type","query","target","sources","overview","分组"]
    # ensure file and upgrade header if needed
    if not os.path.exists(EVENTS_LOG):
        with open(EVENTS_LOG, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(expected_header)
    else:
        # check header
        try:
            with open(EVENTS_LOG, "r", encoding="utf-8") as f:
                rows = [r for r in csv.reader(f)]
            if rows:
                header = rows[0]
                if header != expected_header:
                    # upgrade: pad existing rows to new schema
                    new_rows = [expected_header]
                    for r in rows[1:]:
                        r = r + [""] * max(0, len(expected_header)-len(r))
                        new_rows.append(r[:len(expected_header)])
                    with open(EVENTS_LOG, "w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerows(new_rows)
        except Exception:
            # if any error, rewrite header
            with open(EVENTS_LOG, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(expected_header)
    # serialize fields
    if sources is None:
        sources_str = ""
    else:
        # store as semi-colon list like "1:fname.html;2:fname2.html"
        if isinstance(sources, (list, tuple)):
            items = []
            for i, s in enumerate(sources, start=1):
                items.append(f"{i}:{s}")
            sources_str = ";".join(items)
        else:
            sources_str = str(sources)
    ov = overview_text or ""
    row = [_now(), pid, ev_type, _clean(query, 4000), _clean(target, 4000), _clean(sources_str, 8000), _clean(ov, 16000), str(group)]
    with open(EVENTS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def record_submission(query: str, text: str):
    pid = _get_prolific_id()
    header = ["timestamp", "prolific_id", "query", "word_count", "text"]
    _ensure_csv(SUBMISSIONS_LOG, header)
    wc = len((text or "").split())
    row = [_now(), pid, _clean(query, 2000), str(wc), text or ""]
    with open(SUBMISSIONS_LOG, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def _clean(s, limit=4096):
    try:
        s = s or ""
        if isinstance(s, (list, dict)):
            s = str(s)
        s = s.replace("\r", " ").replace("\n", " ").strip()
    except Exception:
        return (str(s) if s is not None else "")[:limit]
    return s[:limit]

def guess_title_and_text_and_url(html_path):
    try:
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
    except Exception:
        return (os.path.basename(html_path), "", None, os.path.basename(html_path))

    # title
    title = None
    if soup.title and soup.title.text.strip():
        title = soup.title.text.strip()
    else:
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(strip=True)
    if not title:
        title = os.path.basename(html_path)

    # text
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    # always use local route
    dir_name = os.path.basename(os.path.dirname(html_path))
    file_name = os.path.basename(html_path)
    local_url = url_for("serve_page", dir=dir_name, name=file_name, _external=False)

    return (title, text, local_url, file_name)

def load_pages_from_dir(dir_path, limit=60):
    files = sorted(glob.glob(os.path.join(dir_path, "*.html")))
    pages = []
    for fp in files[:limit]:
        title, text, url, fname = guess_title_and_text_and_url(fp)
        pages.append({
            "title": title,
            "text": text,
            "href": url,
            "name": fname,
            "dir": os.path.basename(dir_path),
        })
    return pages

def score_query(text, q):
    if not text or not q:
        return 0
    words = [w.lower() for w in re.findall(r"\b\w+\b", q) if len(w) > 2]
    if not words:
        return 0
    lt = text.lower()
    return sum(lt.count(w) for w in words)

def build_prompt(query, ranked_pages):
    numbered = []
    for i, p in enumerate(ranked_pages, start=1):
        snippet = re.sub(r"\s+", " ", p.get("text","")).strip()
        if len(snippet) > 3000:
            snippet = snippet[:3000] + "…"
        title = p.get("title") or p.get("name") or f"Source {i}"
        numbered.append(f"[{i}] {title}\n{snippet}")
    sources_blob = "\n\n".join(numbered)

    # === EXACT INSTRUCTIONS REQUIRED BY USER ===
    system_rules = (
        "INSTRUCTIONS:\n"
        "Assume that you are the Google AI Overview generator, which is a feature integrated into Google Search that provides AI-generated summaries of search results. "
        "Please answer the following query in one paragraph based on the HTMLs provided. For each factual sentence, append inline citation(s)\n"
        "like [1] or [2][5]. Avoid markdown headings, bullet lists, disclaimers.\n"
        "End with the overview only.\n"
    )

    # We keep QUERY and SOURCES sections, but the instruction text is exactly as above.
    prompt = f"QUERY:\n{query}\n\nSOURCES:\n{sources_blob}\n\n{system_rules}"
    return prompt

def generate_overview(query, pages, max_sources=8):
    # rank pages
    ranked = sorted(pages, key=lambda p: score_query(p.get("text",""), query), reverse=True)[:max_sources]

    prompt = build_prompt(query, ranked)

    # Prepare citations array
    citations = []
    for i, p in enumerate(ranked, start=1):
        href = p.get("href") or ""
        # route clicks via /out for logging
        out_url = url_for("out_click", u=href, title=p.get("title",""), q=query, _external=False)
        citations.append({"idx": i, "title": p.get("title") or p.get("name") or f"Source {i}", "href": out_url})

    overview_html = None

    if _GENAI_READY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            text = (resp.text or "").strip()
            # Ensure it's a single paragraph string
            text = re.sub(r"\n{2,}", " ", text)
            overview_html = f'<div id="overview"><p style="font-size:17px;line-height:1.65">{text}</p></div>'
        except Exception as e:
            overview_html = None

    if not overview_html:
        # Fallback: simple stitched sentence with citations [1], [2], etc.
        parts = []
        for i, p in enumerate(ranked, start=1):
            t = (p.get("title") or "source").split(" | ")[0]
            parts.append(f"{t} [{i}]")
            if len(parts) >= 4:
                break
        sentence = "; ".join(parts) + "." if parts else "No relevant content found."
        overview_html = f'<div id="overview"><p style="font-size:17px;line-height:1.65">{sentence}</p></div>'

    return overview_html, citations

# ------------------------------
# Routes
# ------------------------------
@app.route("/")
def home():
    # If prolific id not set, go to gate
    pid = _get_prolific_id()
    if not pid:
        return render_template("prolific_gate.html", title="Welcome")
    return redirect(url_for("results"))

@app.route("/set_prolific", methods=["POST"])
def set_prolific():
    pid = request.form.get("prolific_id","").strip()
    resp = make_response(redirect(url_for("results")))
    if pid:
        resp.set_cookie("prolific_id", pid, max_age=90*24*3600, httponly=False, samesite="Lax")
    return resp

@app.route("/results")
def results():
    q = request.args.get("q","").strip()
    pid = _get_prolific_id()
    group, dir_path = choose_group_and_dir(pid)
    overview_text = None
    citations = []
    if q:
        pages = load_pages_from_dir(dir_path, limit=80)
        overview_html, citations = generate_overview(q, pages)
        # extract text inside <p> if present
        try:
            soup = BeautifulSoup(overview_html, "html.parser")
            ptag = soup.find("p")
            overview_text = ptag.get_text(" ", strip=True) if ptag else soup.get_text(" ", strip=True)
        except Exception:
            overview_text = None
        # build sources list of local file names in ranked order
        # We reconstruct ranked order similar to generate_overview
        ranked = sorted(pages, key=lambda p: score_query(p.get("text",""), q), reverse=True)[:8]
        src_names = [p.get("name","") for p in ranked]
        record_event("overview", q, f"{len(pages)} candidates from {os.path.basename(dir_path)}", sources=src_names, overview_text=overview_text or "")
    return render_template('results.html', title='Results', query=q, overview=overview_text, citations=citations, prolific_id=pid,)

    return render_template("results.html", title="Results", query=q, overview_html=overview_html, citations=citations, prolific_id=pid)

@app.route("/api/overview", methods=["POST"])
def api_overview():
    data = request.get_json(force=True, silent=True) or {}
    q = (data.get("q") or "").strip()
    pid = _get_prolific_id()
    group, dir_path = choose_group_and_dir(pid)
    if not q:
        return {"error":"missing query"}, 400
    pages = load_pages_from_dir(dir_path, limit=80)
    overview_html, citations = generate_overview(q, pages)
    # build sources list of local file names in ranked order
        # We reconstruct ranked order similar to generate_overview
        ranked = sorted(pages, key=lambda p: score_query(p.get("text",""), q), reverse=True)[:8]
        src_names = [p.get("name","") for p in ranked]
        record_event("overview", q, f"{len(pages)} candidates from {os.path.basename(dir_path)}", sources=src_names, overview_text=overview_text or "")
    # extract text for partial
    try:
        soup = BeautifulSoup(overview_html, "html.parser")
        ptag = soup.find("p")
        overview_text = ptag.get_text(" ", strip=True) if ptag else soup.get_text(" ", strip=True)
    except Exception:
        overview_text = None
    return {
        "overview_html": render_template("_overview_fragment.html", overview=overview_text),
        "citations_html": render_template("_citations_fragment.html", citations=citations, query=q),
    }

@app.route("/out")
def out_click():
    # Log click and redirect to target (local /page?dir=...&name=...)
    url = request.args.get("u","")
    title = request.args.get("title","")
    q = request.args.get("q","")
    # extract local filename if possible
    target_name = ""
    try:
        from urllib.parse import urlparse, parse_qs
        pr = urlparse(url)
        if pr.path == "/page":
            qs = parse_qs(pr.query)
            target_name = (qs.get("name",[None])[0]) or ""
    except Exception:
        target_name = ""
    record_event("click", q, target_name or url)
    # only allow internal /page route
    if url.startswith("/"):
        return redirect(url)
    # fallback to results
    return redirect(url_for("results"))
    # only allow http/https
    if not (url.startswith("http://") or url.startswith("https://")):
        return redirect(url_for("results"))
    return redirect(url)

@app.route("/page")
def serve_page():
    # Serve a saved HTML by dir and name
    dir_name = request.args.get("dir")
    name = request.args.get("name")
    if dir_name not in ("webpages", "webpages2"):
        abort(404)
    directory = WEBPAGES_DIR if dir_name == "webpages" else WEBPAGES2_DIR
    # Security: restrict to *.html
    if not name or "/" in name or not name.endswith(".html"):
        abort(404)
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(directory, name)

# ------------------------------
# Admin (simple cookie gate)
# ------------------------------
def check_admin():
    return request.cookies.get("admin_access") == "1"

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    next_url = request.values.get("next") or url_for("admin_events")
    if request.method == "POST":
        pw = request.form.get("password","").strip()
        if pw == ADMIN_PASSWORD:
            resp = make_response(redirect(next_url))
            resp.set_cookie("admin_access", "1", max_age=24*3600, httponly=True, samesite="Lax")
            return resp
        return render_template("admin_login.html", error="Wrong password.", title="Admin Login", next=next_url), 401
    if check_admin():
        return redirect(next_url)
    return render_template("admin_login.html", title="Admin Login", next=next_url)

@app.route("/admin/logout")
def admin_logout():
    resp = make_response(redirect(url_for("home")))
    resp.delete_cookie("admin_access")
    return resp

@app.route("/admin/events")
def admin_events():
    if not check_admin():
        return redirect(url_for("admin_login", next=url_for("admin_events")))
    rows = []
    if os.path.exists(EVENTS_LOG):
        with open(EVENTS_LOG, "r", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f)]
    return render_template("admin_events.html", title="Admin Events", rows=rows)

@app.route("/admin/events/download")
def admin_events_download():
    if not check_admin():
        return redirect(url_for("admin_login", next=url_for("admin_events_download")))
    if not os.path.exists(EVENTS_LOG):
        with open(EVENTS_LOG, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp","prolific_id","type","query","target","sources","overview","分组"])
    return send_from_directory(LOGS_DIR, os.path.basename(EVENTS_LOG), as_attachment=True)

@app.route("/admin/events/clear", methods=["POST"])
def admin_events_clear():
    if not check_admin():
        return redirect(url_for("admin_login", next=url_for("admin_events")))
    header = ["timestamp","prolific_id","type","query","target","sources","overview","分组"]
    with open(EVENTS_LOG, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(header)
    return redirect(url_for("admin_events"))

@app.route("/admin/logs")
def admin_logs():
    if not check_admin():
        return redirect(url_for("admin_login", next=url_for("admin_logs")))
    rows = []
    if os.path.exists(SUBMISSIONS_LOG):
        with open(SUBMISSIONS_LOG, "r", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f)]
    return render_template("admin_logs.html", title="Admin Submissions", rows=rows)

@app.route("/admin/logs/download")
def admin_logs_download():
    if not check_admin():
        return redirect(url_for("admin_login", next=url_for("admin_logs_download")))
    if not os.path.exists(SUBMISSIONS_LOG):
        _ensure_csv(SUBMISSIONS_LOG, ["timestamp","prolific_id","query","word_count","text"])
    return send_from_directory(LOGS_DIR, os.path.basename(SUBMISSIONS_LOG), as_attachment=True)

@app.route("/admin/logs/clear", methods=["POST"])
def admin_logs_clear():
    if not check_admin():
        return redirect(url_for("admin_login", next=url_for("admin_logs")))
    header = ["timestamp","prolific_id","query","word_count","text"]
    with open(SUBMISSIONS_LOG, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(header)
    return redirect(url_for("admin_logs"))

# ------------------------------
# Submit conclusion (from results page)
# ------------------------------
@app.route("/submit", methods=["POST"])
def submit():
    text = request.form.get("text","").strip()
    q = request.form.get("q","").strip()
    record_submission(q, text)
    record_event("submit", q, f"{len(text.split())} words")
    return render_template("thanks.html", title="Thank you")

# ------------------------------
# Run
# ------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
