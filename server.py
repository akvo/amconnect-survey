"""AMConnect survey: public form at /, passcode-protected dashboard and exports under /admin.

Run: python3 server.py   (settings in .env next to this file, see .env.example)
Edit questions in config/questions.json; the form, validation and dashboard all read it.
"""
import hashlib, hmac, io, json, os, re, sqlite3, sys, time, zipfile
from xml.sax.saxutils import escape
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, parse_qsl

HERE = Path(__file__).parent
# Load KEY=VALUE lines from .env; real environment variables (e.g. hosting secrets) win.
if (HERE / ".env").exists():
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and not k.strip().startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
DB_PATH = os.environ.get("DB_PATH", str(HERE / "survey.db"))
PASSCODE = os.environ.get("ADMIN_PASSCODE", "")
# Session cookie value: derived from the passcode, so changing the passcode logs everyone out.
SESSION = hmac.new(PASSCODE.encode(), b"amconnect-admin", hashlib.sha256).hexdigest()
MAX_BODY = 10_000
MAX_TEXT = 300
MAX_COMMENT = 2000


def load_questions():
    q = json.loads((HERE / "config" / "questions.json").read_text(encoding="utf-8"))
    by_id = {x["id"]: x for x in q["questions"]}
    for x in q["questions"]:
        if "options_from" in x:
            x["options"] = by_id[x["options_from"]]["options"]
    check_codes(q)
    return q


def code(x, o=None):
    """Analysis code: Q2 for a question, Q2.1 for an option. Falls back to ids if a code is missing."""
    qc = x.get("code", x["id"])
    return qc if o is None else f"{qc}.{o.get('code', o['id'])}"


def check_codes(q):
    """Codes keep the analysis stable when wording changes, so they must be present and unique."""
    qcodes = [x.get("code") for x in q["questions"]]
    if None in qcodes or len(set(qcodes)) != len(qcodes):
        raise ValueError(f"config/questions.json: every question needs a unique code, got {qcodes}")
    for x in q["questions"]:
        opts = x.get("options", [])
        for key in ("id", "code"):
            vals = [o.get(key) for o in opts]
            if None in vals or len(set(vals)) != len(vals):
                raise ValueError(f"config/questions.json: {x['code']} options need a unique {key}, got {vals}")


def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS responses (id INTEGER PRIMARY KEY, ts TEXT, src TEXT, data TEXT)")
    # Removed entries are flagged, not deleted, so a mistaken removal can be undone on the manage page.
    if "deleted" not in [c[1] for c in con.execute("PRAGMA table_info(responses)")]:
        con.execute("ALTER TABLE responses ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    return con


def validate(body, q):
    """Return a clean answers dict, or raise ValueError with a reason."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise ValueError("bad payload")
    ans, other = body["answers"], body.get("other") or {}
    if not isinstance(other, dict):
        raise ValueError("bad other")
    text = body.get("text") or {}
    if not isinstance(text, dict):
        raise ValueError("bad text")
    out = {"answers": {}, "other": {}, "text": {}, "email": ""}
    for x in q["questions"]:
        qid, t = x["id"], x["type"]
        if t == "text":
            out["text"][qid] = str(text.get(qid) or "").strip()[:MAX_COMMENT]
            continue
        if t == "email":
            e = str(body.get("email") or "").strip()[:200]
            if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
                raise ValueError("bad email")
            out["email"] = e
            continue
        opts = {o["id"]: o for o in x["options"]}
        v = ans.get(qid)
        picks = [v] if t == "single" else v
        if not isinstance(picks, list) or not picks or not all(isinstance(p, str) for p in picks):
            raise ValueError(f"{qid}: answer required")
        if len(picks) > (1 if t == "single" else x["max"]) or len(set(picks)) != len(picks):
            raise ValueError(f"{qid}: too many or duplicate picks")
        if any(p not in opts for p in picks):
            raise ValueError(f"{qid}: unknown option")
        if len(picks) > 1 and any(opts[p].get("exclusive") for p in picks):
            raise ValueError(f"{qid}: exclusive option combined")
        out["answers"][qid] = picks
        if any(opts[p].get("other") for p in picks):
            out["other"][qid] = str(other.get(qid) or "").strip()[:MAX_TEXT]
    src = str(body.get("src") or "")[:40]
    return out, src if re.fullmatch(r"[A-Za-z0-9_-]*", src) else ""


def score(rows, q, role=None):
    """rows: list of stored dicts. Rank 1 of max N scores N points, rank 2 scores N-1, and so on."""
    by_role = {}  # counted over all responses, so the role filter can show every role's count
    for r in rows:
        k = r["answers"].get("role", ["?"])[0]
        by_role[k] = by_role.get(k, 0) + 1
    total = len(rows)
    if role:
        rows = [r for r in rows if r["answers"].get("role", [None])[0] == role]
    n = len(rows)
    res = {"n": n, "total": total, "small": n < 5, "questions": [], "other": [], "comments": [], "by_role": by_role, "by_src": {}}
    for r in rows:
        res["by_src"][r.get("src") or "direct"] = res["by_src"].get(r.get("src") or "direct", 0) + 1
        for qid, text in r.get("other", {}).items():
            if text:
                res["other"].append({"q": qid, "text": text})
        for qid, text in r.get("text", {}).items():
            if text:
                res["comments"].append({"q": qid, "text": text})
    for x in q["questions"]:
        if x["type"] != "rank":
            continue
        m, stats = x["max"], {o["id"]: [0, 0] for o in x["options"]}
        for r in rows:
            for i, p in enumerate(r["answers"].get(x["id"], [])):
                if p in stats:
                    stats[p][0] += m - i
                    stats[p][1] += 1
        opts = [{"id": o["id"], "code": code(x, o), "label": o["label"], "module": o.get("module", ""),
                 "points": stats[o["id"]][0], "count": stats[o["id"]][1],
                 "share": stats[o["id"]][0] / (n * m) if n else 0,
                 "pct": stats[o["id"]][1] / n if n else 0} for o in x["options"]]
        opts.sort(key=lambda o: -o["points"])
        res["questions"].append({"id": x["id"], "code": code(x), "text": x["text"], "options": opts})
    # Value x difficulty: split each axis at the mean share across the shared options.
    qd = q.get("quadrant")
    if qd:
        get = {s["id"]: {o["id"]: o for o in s["options"]} for s in res["questions"]}
        val, dif = get.get(qd["value"], {}), get.get(qd["difficulty"], {})
        ids = [i for i in val if i in dif and i != "other"]
        if ids:
            mv = sum(val[i]["share"] for i in ids) / len(ids)
            md = sum(dif[i]["share"] for i in ids) / len(ids)
            res["quadrant"] = [{"id": i, "code": val[i]["code"], "label": val[i]["label"], "value": val[i]["share"],
                                "difficulty": dif[i]["share"]} for i in ids]
            res["quadrant_mean"] = {"value": mv, "difficulty": md}
    return res


def table(rows, q):
    """Header and one list per response, for the Excel export."""
    head, qs, lines = ["timestamp", "source"], [], []
    for x in q["questions"]:
        if x["type"] in ("single", "text"):
            head.append(code(x)); qs.append(x)
        elif x["type"] == "rank":
            head += [f"{code(x)}_rank{i + 1}" for i in range(x["max"])]; qs.append(x)
        if any(o.get("other") for o in x.get("options", [])):
            head.append(f"{code(x)}_other")
    has_email = any(x["type"] == "email" for x in q["questions"])
    if has_email:
        head.append("email")
    for r in rows:
        line = [r["ts"], r.get("src", "")]
        for x in qs:
            if x["type"] == "text":
                line.append(r.get("text", {}).get(x["id"], ""))
                continue
            # Answers are stored by option id, so cells always use today's code and wording.
            labels = {o["id"]: f"{code(x, o)} {o['label']}" for o in x["options"]}
            picks = [labels.get(p, p) for p in r["answers"].get(x["id"], [])]
            line += (picks[:1] or [""]) if x["type"] == "single" else picks + [""] * (x["max"] - len(picks))
            if any(o.get("other") for o in x["options"]):
                line.append(r.get("other", {}).get(x["id"], ""))
        if has_email:
            line.append(r.get("email", ""))
        lines.append(line)
    return head, lines


def codebook(q):
    out = [["code", "question", "answer option", "module"]]
    for x in q["questions"]:
        out.append([code(x), x.get("text", ""), "", ""])
        out += [[code(x, o), "", o["label"], o.get("module", "")] for o in x.get("options", [])]
    return out


def export_xlsx(rows, q):
    """Minimal .xlsx (Responses + Codebook sheets) with the standard library only.
    Cells are inline strings, so free text can never run as a formula."""
    bad = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # not allowed in XML; would corrupt the file
    def col(i):
        s = ""
        while i >= 0:
            s, i = chr(65 + i % 26) + s, i // 26 - 1
        return s
    def sheet(data, widths):
        rows_xml = "".join(
            f'<row r="{r + 1}">' + "".join(
                f'<c r="{col(c)}{r + 1}" t="inlineStr"{" s=\"1\"" if r == 0 else ""}><is><t xml:space="preserve">'
                f'{escape(bad.sub("", str(v)))}</t></is></c>' for c, v in enumerate(line)) + "</row>"
            for r, line in enumerate(data))
        cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                f'<cols>{cols}</cols><sheetData>{rows_xml}</sheetData>'
                f'<autoFilter ref="A1:{col(len(data[0]) - 1)}{len(data)}"/></worksheet>')
    head, lines = table(rows, q)
    ns = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{ct}.sheet.main+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{ct}.styles+xml"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{ct}.worksheet+xml"/>'
            f'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="{ct}.worksheet+xml"/></Types>',
        "_rels/.rels": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships {ns}>'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{rel}"><sheets>'
            '<sheet name="Responses" sheetId="1" r:id="rId1"/><sheet name="Codebook" sheetId="2" r:id="rId2"/></sheets>'
            '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
            f"Responses!$A$1:${col(len(head) - 1)}${len(lines) + 1}</definedName></definedNames></workbook>",
        "xl/_rels/workbook.xml.rels": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships {ns}>'
            f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{rel}/worksheet" Target="worksheets/sheet2.xml"/>'
            f'<Relationship Id="rId3" Type="{rel}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet([head] + lines, [22, 10] + [36] * (len(head) - 2)),
        "xl/worksheets/sheet2.xml": sheet(codebook(q), [10, 60, 50, 18]),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    return buf.getvalue()


def all_rows():
    with db() as con:
        return [dict(json.loads(d), ts=ts, src=src) for ts, src, d in
                con.execute("SELECT ts, src, data FROM responses WHERE deleted = 0 ORDER BY id")]


def manage_rows():
    with db() as con:
        return [dict(json.loads(d), id=i, ts=ts, src=src, deleted=bool(dl)) for i, ts, src, d, dl in
                con.execute("SELECT id, ts, src, data, deleted FROM responses ORDER BY id DESC")]


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def authed(self):
        cookies = dict(c.strip().partition("=")[::2] for c in self.headers.get("Cookie", "").split(";") if "=" in c)
        return hmac.compare_digest(cookies.get("admin", "").encode(), SESSION.encode())

    def cookie(self, value, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"admin={value}; Path=/admin; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def redirect(self, to, extra=None):
        self.send(303, "", "text/plain", {"Location": to, **(extra or {})})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self.send(200, (HERE / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/logo.svg":
            return self.send(200, (HERE / "static" / "ASEANEmblem11_RGB.svg").read_bytes(), "image/svg+xml")
        if u.path == "/questions.json":
            return self.send(200, json.dumps(load_questions()))
        if u.path.startswith("/admin"):
            if u.path == "/admin/logout":
                return self.redirect("/admin", {"Set-Cookie": self.cookie("", 0)})
            if not self.authed():
                if u.path in ("/admin", "/admin/", "/admin/manage"):
                    return self.send(200, (HERE / "static" / "login.html").read_bytes(), "text/html; charset=utf-8")
                return self.send(401, json.dumps({"error": "passcode required"}))
            if u.path in ("/admin", "/admin/"):
                return self.send(200, (HERE / "static" / "admin.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/admin/manage":
                return self.send(200, (HERE / "static" / "manage.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/admin/responses.json":
                return self.send(200, json.dumps({"questions_def": load_questions(), "rows": manage_rows()}))
            if u.path == "/admin/results.json":
                role = parse_qs(u.query).get("role", [None])[0]
                q = load_questions()
                return self.send(200, json.dumps({"questions_def": q, **score(all_rows(), q, role)}))
            if u.path == "/admin/export.xlsx":
                name = f"amconnect-survey-{datetime.now():%Y-%m-%d}.xlsx"
                return self.send(200, export_xlsx(all_rows(), load_questions()),
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 {"Content-Disposition": f'attachment; filename="{name}"'})
        self.send(404, "Not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path == "/admin/login":
            size = min(int(self.headers.get("Content-Length") or 0), 1000)
            given = dict(parse_qsl(self.rfile.read(size).decode("utf-8", "replace"))).get("passcode", "").strip()
            if hmac.compare_digest(given.encode(), PASSCODE.encode()):
                return self.redirect("/admin", {"Set-Cookie": self.cookie(SESSION, 12 * 3600)})
            time.sleep(1)  # slows down guessing
            return self.redirect("/admin?wrong=1")
        if urlparse(self.path).path == "/admin/responses/status":
            if not self.authed():
                return self.send(401, json.dumps({"error": "passcode required"}))
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1000)))
                rid, removed = int(body["id"]), bool(body["removed"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                return self.send(400, json.dumps({"error": "bad request"}))
            with db() as con:
                hit = con.execute("UPDATE responses SET deleted = ? WHERE id = ?", (int(removed), rid)).rowcount
            return self.send(200 if hit else 404, json.dumps({"ok": bool(hit)}))
        if urlparse(self.path).path != "/submit":
            return self.send(404, "Not found", "text/plain")
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0 or size > MAX_BODY:
            return self.send(400, json.dumps({"error": "bad size"}))
        try:
            clean, src = validate(json.loads(self.rfile.read(size)), load_questions())
        except (ValueError, json.JSONDecodeError) as e:
            return self.send(400, json.dumps({"error": str(e)}))
        with db() as con:
            con.execute("INSERT INTO responses (ts, src, data) VALUES (?, ?, ?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), src, json.dumps(clean)))
        self.send(200, json.dumps({"ok": True}))


def self_check():
    q = {"quadrant": {"value": "v", "difficulty": "d"}, "questions": [
        {"id": "role", "type": "single", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
        {"id": "v", "type": "rank", "max": 3, "text": "", "options": [{"id": x, "label": x} for x in "xyz"]},
        {"id": "d", "type": "rank", "max": 3, "text": "", "options": [{"id": x, "label": x} for x in "xyz"]},
        {"id": "c", "type": "text", "optional": True, "text": ""}]}
    ok, _ = validate({"answers": {"role": "a", "v": ["x"], "d": ["y"]}, "text": {"c": " hi "}}, q)
    line = table([dict(ok, ts="t")], q)[1][0]
    assert ok["text"] == {"c": "hi"} and line[-1] == "hi" and line[2] == "role.a A", line
    with zipfile.ZipFile(io.BytesIO(export_xlsx([dict(ok, ts="t", text={"c": "a<b&\x01"})], q))) as z:
        import xml.dom.minidom
        for n in z.namelist():
            if n.endswith(".xml") or n.endswith(".rels"):
                xml.dom.minidom.parseString(z.read(n))  # every part must be well-formed XML
        assert "a&lt;b&amp;" in z.read("xl/worksheets/sheet1.xml").decode()
    try:
        check_codes({"questions": [{"id": "a", "code": "Q1"}, {"id": "b", "code": "Q1"}]})
        raise AssertionError("duplicate question code accepted")
    except ValueError:
        pass
    rows = [{"answers": {"role": ["a"], "v": ["x", "y"], "d": ["z"]}},
            {"answers": {"role": ["a"], "v": ["x"], "d": ["x", "z"]}},
            {"answers": {"role": ["b"], "v": ["y", "x", "z"], "d": ["z"]}}]
    r = score(rows, q)
    v = {o["id"]: o for o in r["questions"][0]["options"]}
    assert (v["x"]["points"], v["y"]["points"], v["z"]["points"]) == (8, 5, 1), v
    assert abs(v["x"]["share"] - 8 / 9) < 1e-9 and v["x"]["pct"] == 1
    quad = {e["id"]: (round(e["value"], 3), round(e["difficulty"], 3)) for e in r["quadrant"]}
    assert quad == {"x": (0.889, 0.333), "y": (0.556, 0.0), "z": (0.111, 0.889)}, quad
    rb = score(rows, q, role="b")
    assert rb["n"] == 1 and rb["by_role"] == {"a": 2, "b": 1} and rb["total"] == 3, rb
    for bad in ({"answers": {"role": "a", "v": ["x", "x"], "d": ["z"]}},
                {"answers": {"role": "a", "v": ["w"], "d": ["z"]}},
                {"answers": {"role": "a", "v": ["x", "y", "z", "x"], "d": ["z"]}}):
        try:
            validate(bad, q)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass


if __name__ == "__main__":
    self_check()
    if len(PASSCODE) < 6:
        sys.exit("Set ADMIN_PASSCODE (at least 6 characters) in .env before starting.")
    load_questions()  # fail fast on a broken config/questions.json
    db().close()
    port = int(os.environ.get("PORT", "8000"))
    print(f"Survey on http://localhost:{port}/  (admin: /admin)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
