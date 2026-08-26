import argparse
import json
import os
import random
import re
import threading
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

from . import crawl, process


SCHEMA_KEYS = ["title", "year", "session_type", "speakers", "organizers",
               "moderators", "themes", "sdgs", "policy_questions", "format",
               "duration", "language", "time", "room", "report_link",
               "takeaways", "summary", "keywords"]

DEFAULT_SPEC = {
    "drupal_fields": [],
    "keyword_categories": [],
    "required": ["title", "year", "session_type", "summary", "keywords"],
    "optional": ["speakers", "organizers", "moderators", "themes", "sdgs",
                 "policy_questions", "format", "duration", "language", "time",
                 "room", "report_link", "takeaways"],
}

TYPE_SPECS = {
    "workshop": {
        "drupal_fields": [
            ("field-session-content", "Session content"),
            ("field-theme", "Theme"), ("field-subtheme", "Subtheme"),
            ("field-speakers", "Speakers"), ("field-co-organizers", "Co-organizers"),
            ("field-proposers", "Proposers"), ("field-sdgs", "SDGs"),
            ("field-policy-questions", "Policy question(s)"),
            ("field-discussion-facilitation", "Discussion facilitation"),
            ("field-format", "Format"), ("field-issues", "Issues"),
            ("field-outcomes", "Outcomes"),
            ("field-key-session-takeaways", "Key session takeaways"),
            ("field-body", "Body (legacy years)"),
            ("field-rapporteur", "Rapporteur (legacy years)"),
        ],
        "keyword_categories": [
            ("theme/subtheme", "official IGF theme and sub-theme"),
            ("policy question", "the concrete policy questions the session addresses"),
            ("speaker", "speaker names, organisations and roles"),
            ("organizer", "co-organisers and proposers with organisation type: government, private sector, civil society, technical community, academia, international organisation"),
            ("sdg", "Sustainable Development Goal tags"),
            ("format", "panel / roundtable / debate / breakout"),
            ("takeaway/outcome", "expected outcomes and key session takeaways"),
        ],
    },
    "open-forum": {
        "drupal_fields": [
            ("field-description-of", "Description"),
            ("field-theme-of", "Theme"), ("field-subtheme-of", "Subtheme"),
            ("field-organizers-of", "Organizers"), ("field-speakers-of", "Speakers"),
            ("field-online-moderator-of", "Online moderator"),
            ("field-onsite-moderator-of", "Onsite moderator"),
            ("field-rapporteur-of", "Rapporteur"),
            ("field-background-paper-of", "Background paper"),
            ("field-sdgs-of", "SDGs"),
            ("field-organization-website-of", "Organization website"),
            ("field-report", "Report"), ("field-call-to-action", "Call to action"),
            ("field-key-session-takeaways", "Key session takeaways"),
            ("field-format-of", "Format"), ("field-time", "Time"),
            ("field-room", "Room"), ("field-gender", "Gender"),
            ("field-women", "Women"), ("field-participants", "Participants"),
        ],
        "keyword_categories": [
            ("description", "forum background, objectives and agenda overview"),
            ("theme/subtheme", "official IGF theme and sub-theme"),
            ("organizer", "hosting organisation and its type; Open Forums are usually run by international organisations or large corporations"),
            ("speaker", "speaker names and organisations"),
            ("moderator", "online vs onsite moderator, useful for pre/post COVID comparison"),
            ("rapporteur", "rapporteur who writes the post-session report"),
            ("background paper", "linked academic or policy paper, the only citation-bearing field"),
            ("sdg", "Sustainable Development Goal tags"),
            ("report/call-to-action/takeaway", "post-session report, action calls and key takeaways"),
        ],
    },
    "lightning-talk": {
        "drupal_fields": [
            ("field-description-0", "Description"), ("field-speakers-0", "Speakers"),
            ("field-format-0", "Format"), ("field-duration-0", "Duration"),
            ("field-language", "Language"), ("field-time", "Time"),
            ("field-room", "Room"), ("field-rapporteur-0", "Rapporteur"),
            ("field-organizers-0", "Organizers"),
        ],
        "keyword_categories": [
            ("description", "the talk content in a few sentences"),
            ("speaker", "1-3 speakers"),
            ("format", "talk / demo / pitch"),
            ("duration", "usually 5-15 minutes"),
            ("language", "multilingual marker beyond English"),
            ("time/room", "slot and room on the schedule"),
            ("rapporteur", "rapporteur for the post-session summary"),
        ],
    },
    "day-0-event": {
        "drupal_fields": [
            ("field-organizers-0", "Organizers"), ("field-description-0", "Description"),
            ("field-speakers-0", "Speakers"), ("field-format-0", "Format"),
            ("field-onsite-moderator-0", "Onsite moderator"),
            ("field-online-moderator-0", "Online moderator"),
            ("field-organization-website-0", "Organization website"),
            ("field-duration-0", "Duration"), ("field-rapporteur-0", "Rapporteur"),
            ("field-time", "Time"), ("field-room", "Room"),
        ],
        "keyword_categories": [
            ("description", "the main content carrier for day 0 events"),
            ("organizer", "usually an academic institution or NRI"),
            ("speaker/moderator", "speakers and online/onsite moderators"),
            ("duration", "often missing; useful as a data-quality indicator"),
            ("time/room", "frequently missing in 20-45 percent of pages"),
        ],
    },
    "launch-award": {
        "drupal_fields": [
            ("field-description-0", "Description"), ("field-organizers-0", "Organizers"),
            ("field-speakers-0", "Speakers"), ("field-report", "Report"),
            ("field-call-to-action", "Call to action"),
            ("field-key-session-takeaways", "Key session takeaways"),
            ("field-duration-0", "Duration"), ("field-participants", "Participants"),
            ("field-women", "Women"), ("field-time", "Time"), ("field-room", "Room"),
        ],
        "keyword_categories": [
            ("description", "what is launched or awarded"),
            ("organizer/speaker", "launching institution and presenters"),
            ("report/takeaway", "linked report and key points"),
            ("duration", "usually 30-60 minutes"),
            ("participants/women", "audience size and gender statistics"),
        ],
    },
    "networking": {
        "drupal_fields": [
            ("field-description-0", "Description"), ("field-organizers-0", "Organizers"),
            ("field-speakers-0", "Speakers"), ("field-format-0", "Format"),
            ("field-report", "Report"),
            ("field-key-session-takeaways", "Key session takeaways"),
            ("field-call-to-action", "Call to action"),
            ("field-duration-0", "Duration"), ("field-rapporteur-0", "Rapporteur"),
            ("field-time", "Time"), ("field-room", "Room"),
        ],
        "keyword_categories": [
            ("description", "topic and purpose of the networking session"),
            ("organizer", "initiating community, often a Dynamic Coalition or NRI"),
            ("speaker", "discussion leaders"),
            ("format", "roundtable / open exchange"),
            ("report/takeaway/call-to-action", "post-session summary and next steps"),
        ],
    },
    "town-hall": {
        "drupal_fields": [
            ("field-issue-of", "Issue(s)"), ("field-time", "Time"),
            ("field-room", "Room"), ("field-report", "Report"),
            ("field-speakers", "Speakers"), ("field-description", "Description"),
        ],
        "keyword_categories": [
            ("issue", "the social issues listed for discussion; the unique Town Hall field, more social than the workshop policy questions"),
            ("time/room", "exact slot and room, enables venue-density analysis"),
            ("report", "medium-frequency post-session report"),
        ],
    },
    "main-session": {
        "drupal_fields": [],
        "keyword_categories": [
            ("session kind", "plenary / main session / high-level / ministerial"),
            ("ceremony", "opening ceremony, closing ceremony, open mic"),
            ("track", "parliamentary track, high level leaders track"),
        ],
    },
    "transcript": {
        "drupal_fields": [],
        "keyword_categories": [
            ("speaker", "speaker names at line starts, for diarisation and stance analysis"),
            ("session title", "the session this verbatim record belongs to"),
            ("date/venue", "meeting date and city"),
        ],
    },
    "schedule": {
        "drupal_fields": [],
        "keyword_categories": [
            ("agenda", "day-by-day agenda density: sessions per day"),
            ("slot", "time and room per session"),
            ("session title", "titles enable topic timeline analysis"),
        ],
    },
    "participants": {
        "drupal_fields": [],
        "keyword_categories": [
            ("name", "participant name"),
            ("affiliation", "institution name for demographic analysis"),
            ("country", "geographic distribution"),
        ],
    },
    "report": {
        "drupal_fields": [],
        "keyword_categories": [
            ("executive summary", "top-level conclusions"),
            ("recommendation", "actionable recommendations"),
            ("key finding", "core findings"),
            ("chair summary", "official chair summary"),
        ],
    },
    "dc-bpf-nri": {
        "drupal_fields": [],
        "keyword_categories": [
            ("community", "Dynamic Coalition / Best Practice Forum / NRI identity"),
            ("intersessional", "intersessional work plan"),
            ("output", "output documents produced across years"),
        ],
    },
    "high-level": {
        "drupal_fields": [],
        "keyword_categories": [
            ("leadership", "ministerial / leadership panel participants"),
            ("commitment", "policy commitments made"),
            ("track", "high level track / parliamentary track"),
        ],
    },
    "newcomers": {
        "drupal_fields": [],
        "keyword_categories": [("orientation", "newcomer orientation and IGF introduction")],
    },
    "news": {
        "drupal_fields": [],
        "keyword_categories": [("newsletter", "newsletter issue and topics")],
    },
    "press": {
        "drupal_fields": [],
        "keyword_categories": [("press release", "press release / media advisory content")],
    },
    "calls": {
        "drupal_fields": [],
        "keyword_categories": [
            ("call", "call for proposals / submissions"),
            ("deadline", "submission deadline"),
        ],
    },
    "capacity-building": {
        "drupal_fields": [],
        "keyword_categories": [("training", "capacity development / training topics")],
    },
    "policy-networks": {
        "drupal_fields": [],
        "keyword_categories": [("policy network", "policy network scope and members")],
    },
    "donors": {
        "drupal_fields": [],
        "keyword_categories": [("donor", "donor and trust fund contribution")],
    },
    "mag-eg": {
        "drupal_fields": [],
        "keyword_categories": [("governance", "MAG / expert group meeting record")],
    },
    "village": {
        "drupal_fields": [],
        "keyword_categories": [("booth", "village booth / exhibition / showcase")],
    },
    "youth": {
        "drupal_fields": [],
        "keyword_categories": [("youth", "youth programme / fellowship")],
    },
    "about": {
        "drupal_fields": [],
        "keyword_categories": [("mandate", "IGF mandate / about / FAQ")],
    },
    "accessibility": {
        "drupal_fields": [],
        "keyword_categories": [("accessibility", "accessibility / disability / inclusion")],
    },
}

_KEYS_DESC = {
    "title": "string, page title",
    "year": "int",
    "session_type": "string, meeting type",
    "speakers": '[{"name": "string", "organization": "string or null", "role": "string or null"}]',
    "organizers": "[string]",
    "moderators": "[string]",
    "themes": "[string]",
    "sdgs": "[string]",
    "policy_questions": "[string]",
    "format": "string or null",
    "duration": "string or null",
    "language": "string or null",
    "time": "string or null",
    "room": "string or null",
    "report_link": "string or null",
    "takeaways": "[string]",
    "summary": "string, at most 60 words",
    "keywords": '[{"kw": "string", "evidence": "verbatim substring of the page text"}]',
}


def spec_for(page_type):
    spec = dict(DEFAULT_SPEC)
    spec.update(TYPE_SPECS.get(page_type) or {})
    return spec


def schema_for(page_type):
    spec = spec_for(page_type)
    desc = {k: _KEYS_DESC[k] for k in SCHEMA_KEYS}
    return json.dumps(desc, indent=1) + "\nRequired keys: " + ", ".join(spec["required"])


def build_system_prompt(page_type):
    spec = spec_for(page_type)
    lines = [
        "You extract structured data from a saved IGF meeting page of type '%s'." % page_type,
        "Use only information explicitly present in the page text. If a field is absent use null for strings and [] for lists.",
        "Keep speaker and organisation names in their original spelling.",
        "Give up to 10 keywords as short phrases of 1-5 words each: as many as the page text genuinely supports, with no padding. Every keyword needs evidence: an exact verbatim substring of the page text.",
        "Answer with valid JSON only, no explanation.",
    ]
    if spec["keyword_categories"]:
        lines.append("Preferred keyword categories: " + "; ".join(
            "%s (%s)" % (name, why) for name, why in spec["keyword_categories"]))
    return "\n".join(lines)


def build_user_prompt(page_type, title, body_text, drupal_fields):
    drupal_json = json.dumps(drupal_fields, ensure_ascii=False, indent=1) if drupal_fields else "{}"
    return (
        "TYPE: %s\nTITLE: %s\n\nDRUPAL FIELDS (field name -> items):\n%s\n\n"
        "PAGE TEXT:\n%s\n\nOutput only JSON matching this schema:\n%s"
    ) % (page_type, title, drupal_json, body_text, schema_for(page_type))



OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
TIMEOUT = 300
BODY_HEAD = 9000
BODY_TAIL = 3000

def _ollama_chat(model, messages, temperature=0.0, attempts=2):
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": temperature}}
    if model.lower().startswith("qwen3"):
        payload["think"] = False
    req = urllib.request.Request(
        OLLAMA_CHAT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    last = None
    for _ in range(attempts):
        try:
            data = json.load(urllib.request.urlopen(req, timeout=TIMEOUT))
            msg = data.get("message") or {}
            return msg, {"eval_count": data.get("eval_count"),
                         "total_duration": data.get("total_duration")}
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(3)
    return {"error": last or "unknown"}, {}


def _parse_json_obj(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


def _page_payload(path, src_root):
    rel = os.path.relpath(path, src_root).replace("\\", "/")
    parts = rel.split("/")
    ptype = parts[0] if parts and parts[0] in TYPE_SPECS else None
    if ptype is None:
        ptype = process._classify_by_filename(os.path.basename(path))
    if ptype is None:
        ptype = "other"
    year = process._extract_year(os.path.basename(path)) or process._extract_year(rel)
    raw = open(path, encoding="utf-8", errors="ignore").read()
    soup = BeautifulSoup(raw, "html.parser")
    crawl._strip_noise(soup)
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    drupal = crawl._extract_drupal_fields_json(soup)
    main = soup.find("main") or soup.find(id="main-content") or soup.find("body")
    body = re.sub(r"\s+", " ", main.get_text(separator=" ", strip=True)) if main else ""
    if len(body) > BODY_HEAD + BODY_TAIL:
        body = body[:BODY_HEAD] + " ... [truncated] ... " + body[-BODY_TAIL:]
    drupal_brief = {k: [it.get("text", "")[:400] for it in v.get("content", [])][:4]
                    for k, v in drupal.items()}
    return rel, ptype, year, title, body, drupal_brief


def _extract_one(path, src_root, model, out_dir, lock):
    rel = os.path.relpath(path, src_root).replace("\\", "/")
    try:
        _, ptype, year, title, body, drupal_brief = _page_payload(path, src_root)
        if not body.strip():
            rec = {"file": os.path.basename(path), "rel_path": rel, "type": ptype,
                   "year": year, "model": model, "status": "skip",
                   "error": "empty body", "result": None}
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            with lock:
                with open(os.path.join(out_dir, "extraction.jsonl"), "a", encoding="utf-8") as f:
                    f.write(line)
            return rec
        messages = [
            {"role": "system", "content": build_system_prompt(ptype)},
            {"role": "user", "content": build_user_prompt(ptype, title, body, drupal_brief)},
        ]
        t0 = time.time()
        msg, stats = _ollama_chat(model, messages)
        latency = time.time() - t0
        if "error" in msg:
            return {"file": os.path.basename(path), "rel_path": rel, "type": ptype,
                    "year": year, "model": model, "status": "error",
                    "error": msg["error"], "latency": latency, "result": None}
        try:
            result = _parse_json_obj(msg.get("content") or "")
        except Exception as e:
            return {"file": os.path.basename(path), "rel_path": rel, "type": ptype,
                    "year": year, "model": model, "status": "error",
                    "error": str(e), "latency": latency,
                    "raw_head": (msg.get("content") or "")[:200], "result": None}
        if result is None:
            return {"file": os.path.basename(path), "rel_path": rel, "type": ptype,
                    "year": year, "model": model, "status": "error",
                    "error": "model returned null", "latency": latency, "result": None}
        rec = {"file": os.path.basename(path), "rel_path": rel, "type": ptype,
               "year": year, "model": model, "status": "ok", "latency": latency,
               "body_chars": len(body), "prompt_chars": len(messages[1]["content"]),
               "eval_count": stats.get("eval_count"), "result": result}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with lock:
            with open(os.path.join(out_dir, "extraction.jsonl"), "a", encoding="utf-8") as f:
                f.write(line)
        return rec
    except Exception as e:
        return {"file": os.path.basename(path), "rel_path": rel, "type": None,
                "year": None, "model": model, "status": "error",
                "error": "%s: %s" % (type(e).__name__, e), "result": None}


def _load_done(out_dir):
    done = set()
    jpath = os.path.join(out_dir, "extraction.jsonl")
    if os.path.exists(jpath):
        with open(jpath, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("rel_path") and r.get("status") in ("ok", "skip"):
                        done.add(r["rel_path"].replace("\\", "/"))
                except Exception:
                    continue
    return done


def full_extract_run(args):
    src = os.path.abspath(args.classified)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    done = _load_done(out)
    files = [str(p) for p in Path(src).rglob("*.html") if "_invalid" not in str(p)]
    done_n = sum(1 for f in files if os.path.relpath(f, src).replace("\\", "/") in done)
    todo = [f for f in files if os.path.relpath(f, src).replace("\\", "/") not in done]
    if args.limit:
        todo = todo[:args.limit]
    print("[FULL-EXTRACT] model=%s workers=%d total=%d resume-skipped=%d todo=%d" % (
        args.model, args.workers, len(files), done_n, len(todo)))
    if not todo:
        print("[FULL-EXTRACT] nothing to do")
        return 0
    lock = threading.Lock()
    ok = err = skip = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_extract_one, f, src, args.model, out, lock): f for f in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if rec.get("status") == "ok":
                ok += 1
            elif rec.get("status") == "skip":
                skip += 1
            else:
                err += 1
                line = json.dumps(rec, ensure_ascii=False) + "\n"
                with lock:
                    with open(os.path.join(out, "extraction.jsonl"), "a", encoding="utf-8") as f:
                        f.write(line)
                    with open(os.path.join(out, "failures.tsv"), "a", encoding="utf-8") as ff:
                        ff.write("%s\t%s\n" % (rec.get("rel_path", ""), rec.get("error", "")))
            if i % 20 == 0 or i == len(todo):
                rate = i / max(1.0, (time.time() - t0) / 60.0)
                print("  [%d/%d] ok=%d err=%d skip=%d (%.1f pages/min)" % (i, len(todo), ok, err, skip, rate), flush=True)
    print("[FULL-EXTRACT] done ok=%d err=%d skip=%d -> %s" % (ok, err, skip, out))
    return 0


def full_extract_main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--classified", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    return full_extract_run(args)



_LIST_FIELDS = ["speakers", "organizers", "moderators", "themes", "sdgs",
                "policy_questions", "takeaways"]
_STR_FIELDS = ["title", "session_type", "format", "duration", "language",
               "time", "room", "report_link", "summary"]


def _verify_norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower())


def _tokens(s):
    return set(t for t in _verify_norm(s).split() if len(t) > 1)


def load_jsonl(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def schema_issues(rec):
    issues = []
    result = rec.get("result") or {}
    ptype = rec.get("type") or result.get("session_type") or "other"
    spec = spec_for(ptype)
    for key in spec["required"]:
        if key not in result or result[key] in (None, "", []):
            issues.append({"field": key, "issue": "missing_required"})
    year = result.get("year")
    if year is not None:
        if not isinstance(year, int) or not (1990 <= year <= 2035):
            issues.append({"field": "year", "issue": "bad_value"})
    else:
        issues.append({"field": "year", "issue": "missing_required"})
    for key in _LIST_FIELDS:
        if key in result and result[key] is not None:
            if not isinstance(result[key], list):
                issues.append({"field": key, "issue": "wrong_type"})
            elif key == "speakers":
                for sp in result[key]:
                    if not isinstance(sp, dict) or not sp.get("name"):
                        issues.append({"field": key, "issue": "bad_speaker_entry"})
    for key in _STR_FIELDS:
        if key in result and result[key] is not None and not isinstance(result[key], str):
            issues.append({"field": key, "issue": "wrong_type"})
    kws = result.get("keywords")
    if kws is not None:
        if not isinstance(kws, list):
            issues.append({"field": "keywords", "issue": "wrong_type"})
        elif len(kws) > 10:
            issues.append({"field": "keywords", "issue": "count_above_10"})
        else:
            for kw in kws:
                if not isinstance(kw, dict) or not str(kw.get("kw", "")).strip():
                    issues.append({"field": "keywords", "issue": "bad_entry"})
    return issues


def source_body(path):
    try:
        raw = open(path, encoding="utf-8", errors="ignore").read()
        soup = BeautifulSoup(raw, "html.parser")
        crawl._strip_noise(soup)
        main = soup.find("main") or soup.find(id="main-content") or soup.find("body")
        return re.sub(r"\s+", " ", main.get_text(separator=" ", strip=True)) if main else ""
    except Exception:
        return ""


def grounding_issues(rec, classified_dir):
    out = []
    result = rec.get("result") or {}
    path = os.path.join(classified_dir, rec.get("rel_path", ""))
    body = _verify_norm(source_body(path))
    for kw in result.get("keywords") or []:
        ev = kw.get("evidence")
        if not ev or _verify_norm(str(ev)) not in body:
            out.append({"kw": kw.get("kw"), "evidence": str(ev)[:100]})
    return out


def _soft_f1(gold_kws, pred_kws):
    gt = set(); pr = set()
    for k in gold_kws:
        gt |= _tokens(k)
    for k in pred_kws:
        pr |= _tokens(k)
    if not gt and not pr:
        return 1.0
    if not gt or not pr:
        return 0.0
    inter = len(gt & pr)
    prec = inter / len(pr)
    rec = inter / len(gt)
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def _jaccard(a, b):
    a = {_verify_norm(x) for x in a}
    b = {_verify_norm(x) for x in b}
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def gold_metrics(recs, gold_path, classified_dir):
    gold = json.load(open(gold_path, encoding="utf-8-sig"))
    gold = gold if isinstance(gold, list) else gold.get("docs", [])
    by_file = {}
    for g in gold:
        by_file.setdefault(os.path.basename(g.get("file", "")), []).append(g)
    per = []
    matched = 0
    for rec in recs:
        if rec.get("status") != "ok":
            continue
        cands = by_file.get(rec.get("file", ""), [])
        if not cands:
            continue
        g = cands[0]
        matched += 1
        gkws = [k.get("kw", "") for k in g.get("keywords", [])]
        pkws = [k.get("kw", "") for k in (rec.get("result") or {}).get("keywords", [])]
        res = rec.get("result") or {}
        per.append({
            "rel_path": rec.get("rel_path"), "doc": g.get("doc"),
            "soft_f1": _soft_f1(gkws, pkws),
            "jaccard": _jaccard(gkws, pkws),
            "title_match": _verify_norm(g.get("fields", {}).get("title", "")) == _verify_norm(res.get("title", "")),
        })
    if not per:
        return {"matched": 0}
    return {
        "matched": matched,
        "soft_f1_mean": sum(p["soft_f1"] for p in per) / len(per),
        "jaccard_mean": sum(p["jaccard"] for p in per) / len(per),
        "title_accuracy": sum(1 for p in per if p["title_match"]) / len(per),
        "details": per,
    }


def verify_extract_run(args):
    recs = load_jsonl(args.extraction)
    ok = [r for r in recs if r.get("status") == "ok"]
    err = [r for r in recs if r.get("status") != "ok"]
    print("[VERIFY] records=%d ok=%d error=%d" % (len(recs), len(ok), len(err)))

    all_issues = []
    grounding = []
    field_err = {f: 0 for f in (["keywords", "year"] + _LIST_FIELDS + _STR_FIELDS)}
    for r in ok:
        issues = schema_issues(r)
        all_issues.extend({"rel_path": r.get("rel_path"), **i} for i in issues)
        for i in issues:
            field_err[i["field"]] += 1
        g = grounding_issues(r, args.classified)
        if g:
            grounding.append({"rel_path": r.get("rel_path"), "hits": g})
            field_err["keywords"] += len(g)
    n_ok = len(ok) or 1
    print("  schema issues=%d over %d records" % (len(all_issues), len(ok)))
    print("  ungrounded evidence=%d in %d records" % (sum(len(x['hits']) for x in grounding), len(grounding)))

    ranking = sorted(field_err.items(), key=lambda kv: -kv[1])
    print("  per-field error ranking:")
    for f, n in ranking:
        if n:
            print("    %-18s %d  (%.1f%% of records)" % (f, n, 100.0 * n / n_ok))

    patterns = {"parse_fail": len(err), "schema_violation": 0, "hallucination": 0,
                "omission": 0, "mismatch": 0, "ok": 0}
    for r in ok:
        if schema_issues(r):
            patterns["schema_violation"] += 1
    for r in ok:
        if grounding_issues(r, args.classified):
            patterns["hallucination"] += 1
    gm = {}
    if args.gold and os.path.exists(args.gold):
        gm = gold_metrics(ok, args.gold, args.classified)
        if gm.get("matched"):
            print("  gold: matched=%d soft_f1=%.3f jaccard=%.3f title_acc=%.1f%%" % (
                gm["matched"], gm["soft_f1_mean"], gm["jaccard_mean"],
                100.0 * gm["title_accuracy"]))
            for p in gm["details"]:
                if p["soft_f1"] < 0.4:
                    print("    low-f1: %-60s f1=%.2f" % (p["rel_path"], p["soft_f1"]))
    for r in ok:
        if not schema_issues(r) and not grounding_issues(r, args.classified):
            patterns["ok"] += 1
    print("  error patterns: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(patterns.items())))

    report = {
        "totals": {"records": len(recs), "ok": len(ok), "error": len(err)},
        "schema_issues": all_issues,
        "grounding_issues": grounding,
        "field_error_counts": dict(field_err),
        "error_patterns": patterns,
        "gold_metrics": gm,
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "verification_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("[VERIFY] report -> %s" % os.path.join(args.out, "verification_report.json"))
    return 0


def verify_extract_main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", required=True, help="extraction.jsonl")
    ap.add_argument("--classified", required=True, help="classified HTML root for grounding")
    ap.add_argument("--gold", default="", help="optional gold JSON for similarity")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return verify_extract_run(args)




def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _body_text(path, limit=60000):
    try:
        raw = open(path, encoding="utf-8", errors="ignore").read()
        soup = BeautifulSoup(raw, "html.parser")
        crawl._strip_noise(soup)
        main = soup.find("main") or soup.find(id="main-content") or soup.find("body")
        text = re.sub(r"\s+", " ", main.get_text(separator=" ", strip=True)) if main else ""
        return text[:limit]
    except Exception:
        return ""


def _page_info(classified_dir, path):
    rel = os.path.relpath(path, classified_dir).replace("\\", "/")
    parts = rel.split("/")
    ptype = parts[0] if parts and parts[0] in TYPE_SPECS else "other"
    if ptype not in TYPE_SPECS:
        ptype = process._classify_by_filename(os.path.basename(path)) or "other"
    year = None
    for seg in parts:
        m = re.match(r"^(20\d{2})$", seg)
        if m:
            year = int(m.group(1))
            break
    if year is None:
        year = process._extract_year(os.path.basename(path))
    return rel, ptype, year


def scan(classified_dir):
    pool = []
    for root, dirs, files in os.walk(classified_dir):
        if "_invalid" in root.split(os.sep):
            continue
        for f in files:
            if not f.endswith(".html"):
                continue
            path = os.path.join(root, f)
            rel, ptype, year = _page_info(classified_dir, path)
            if year is None or not (2006 <= int(year) <= 2025):
                continue
            pool.append({"rel": rel, "type": ptype, "year": int(year),
                         "path": path, "len": 0})
    return pool


def _fill_lengths(pool):
    for d in pool:
        d["len"] = len(_body_text(d["path"]))
    return pool


def sample(pool, target=48, seed=42, min_body=800):
    pool = [d for d in pool if d["len"] >= min_body]
    if not pool:
        return []
    cells = {}
    for d in pool:
        cells.setdefault((d["type"], d["year"]), []).append(d)
    for docs in cells.values():
        docs.sort(key=lambda d: (-d["len"], d["rel"]))
    picked = []
    if len(cells) <= target:
        for key in sorted(cells):
            picked.append(cells[key][0])
        rest = [d for key, docs in sorted(cells.items()) for d in docs[1:]]
        rng = random.Random(seed)
        rng.shuffle(rest)
        while len(picked) < target and rest:
            d = rest.pop()
            cell = (d["type"], d["year"])
            if sum(1 for x in picked if (x["type"], x["year"]) == cell) >= 3:
                continue
            picked.append(d)
        return sorted(picked, key=lambda d: (d["type"], d["year"]))
    by_type = {}
    for key, docs in cells.items():
        by_type.setdefault(key[0], []).append((key, docs))
    for lst in by_type.values():
        lst.sort(key=lambda kv: -len(kv[1]))
    types = sorted(by_type, key=lambda t: (-len(by_type[t]), t))
    idx = {t: 0 for t in types}
    chosen = set()
    while len(picked) < target:
        progressed = False
        for t in types:
            if len(picked) >= target:
                break
            lst = by_type[t]
            while idx[t] < len(lst) and lst[idx[t]][0] in chosen:
                idx[t] += 1
            if idx[t] >= len(lst):
                continue
            cell, docs = lst[idx[t]]
            idx[t] += 1
            picked.append(docs[0])
            chosen.add(cell)
            progressed = True
        if not progressed:
            break
    if len(picked) < target:
        rest = [d for key, docs in sorted(cells.items()) for d in docs[1:]]
        rng = random.Random(seed)
        rng.shuffle(rest)
        for d in rest:
            if len(picked) >= target:
                break
            cell = (d["type"], d["year"])
            if sum(1 for x in picked if (x["type"], x["year"]) == cell) >= 3:
                continue
            picked.append(d)
    return sorted(picked, key=lambda d: (d["type"], d["year"]))


def _cmd_sample(args):
    if not os.path.isdir(args.classified):
        print("[ERR] classified dir not found: %s" % args.classified)
        return 2
    pool = _fill_lengths(scan(args.classified))
    print("[INFO] scanned %d html pages with valid years" % len(pool))
    picked = sample(pool, target=args.target, seed=args.seed, min_body=args.min_body)
    os.makedirs(args.window_dir, exist_ok=True)
    rows = ["doc\tfile\tyear\tvenue\tsession_type\trel_path\twindow_chars"]
    for i, d in enumerate(picked, 1):
        doc_id = "doc_%s_%d_%02d" % (d["type"], d["year"], i)
        text = _body_text(d["path"])
        win = text[:args.window_chars]
        with open(os.path.join(args.window_dir, doc_id + ".txt"), "w", encoding="utf-8") as wf:
            wf.write(win)
        rows.append("\t".join([doc_id, os.path.basename(d["path"]), str(d["year"]),
                                "", d["type"], d["rel"], str(len(win))]))
    with open(args.out, "w", encoding="utf-8", newline="") as of:
        of.write("\n".join(rows) + "\n")
    print("[OK] sampled %d docs from %d cells -> %s" % (len(picked), len({(d['type'], d['year']) for d in pool}), args.out))
    print("     windows -> %s/" % args.window_dir)
    for (t, y), n in sorted(Counter((d["type"], d["year"]) for d in picked).items()):
        print("       %-16s %d x%d" % (t, y, n))
    return 0


def _kappa(am, bm):
    keys = sorted(set(am) & set(bm))
    if not keys:
        print("[ERR] no common doc ids"); return 2
    po_sum = pe_sum = 0.0
    n_docs = 0
    exact = 0
    jac_sum = 0.0
    f1_sum = 0.0
    agree05 = 0
    rows = []
    for k in keys:
        ka = {_norm(x["kw"]) for x in am[k].get("keywords", []) if _norm(x["kw"])}
        kb = {_norm(x["kw"]) for x in bm[k].get("keywords", []) if _norm(x["kw"])}
        if ka == kb:
            exact += 1
        if ka or kb:
            jac = len(ka & kb) / len(ka | kb)
            jac_sum += jac
            if jac >= 0.5:
                agree05 += 1
        ta = set()
        for x in ka:
            ta.update(x.split())
        tb = set()
        for x in kb:
            tb.update(x.split())
        if ta or tb:
            inter = len(ta & tb)
            f1_sum += 2 * inter / max(1, len(ta) + len(tb))
        items = sorted(ka | kb)
        n = len(items)
        if n == 0:
            rows.append((k, None, 0.0))
            continue
        agree = sum(1 for it in items if (it in ka) == (it in kb))
        po = agree / n
        p_a = len(ka) / n
        p_b = len(kb) / n
        pe = p_a * p_b + (1 - p_a) * (1 - p_b)
        po_sum += po; pe_sum += pe; n_docs += 1
        rows.append((k, (po - pe) / (1 - pe) if pe < 1 else 1.0, len(ka & kb) / n))
    po = po_sum / n_docs if n_docs else 0.0
    pe = pe_sum / n_docs if n_docs else 0.0
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    print("[KAPPA] docs=%d Po=%.3f Pe=%.3f Kappa=%.3f" % (n_docs, po, pe, kappa))
    print("        exact-set agreement=%.1f%%  mean Jaccard=%.3f  mean soft-F1=%.3f" % (
        100.0 * exact / len(keys), jac_sum / len(keys), f1_sum / len(keys)))
    print("        docs with Jaccard>=0.5: %d/%d (%.1f%%)" % (agree05, len(keys), 100.0 * agree05 / len(keys)))
    print("        interpretation (Landis & Koch 1977): 0.0-0.2 slight, 0.2-0.4 fair,")
    print("        0.4-0.6 moderate, 0.6-0.8 substantial, >0.8 near-perfect")
    print("        note: keyword selection has high item prevalence, which deflates item-wise")
    print("        kappa (prevalence effect); report mean Jaccard / soft-F1 as the primary metrics.")
    for k, kv, j in rows:
        if j < 0.5:
            print("    low-agreement doc: %-32s jaccard=%.2f" % (k, j))
    return 0


def _cmd_kappa(args):
    a = json.load(open(args.gold_a, encoding="utf-8-sig"))
    b = json.load(open(args.gold_b, encoding="utf-8-sig"))
    a = a if isinstance(a, list) else a.get("docs", [])
    b = b if isinstance(b, list) else b.get("docs", [])
    return _kappa({d["doc"]: d for d in a}, {d["doc"]: d for d in b})


def _cmd_stats(args):
    if not os.path.isdir(args.classified):
        print("[ERR] classified dir not found: %s" % args.classified)
        return 2
    pool = _fill_lengths(scan(args.classified))
    cells = {}
    for d in pool:
        cells.setdefault(d["type"], {}).setdefault(d["year"], 0)
        cells[d["type"]][d["year"]] += 1
    for t in sorted(cells):
        years = ",".join("%d:%d" % (y, n) for y, n in sorted(cells[t].items()))
        print("%-16s %s" % (t, years))
    print("[INFO] total %d pages across %d type-year cells" % (len(pool), len({(d['type'], d['year']) for d in pool})))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("sample")
    p1.add_argument("classified")
    p1.add_argument("--target", type=int, default=48)
    p1.add_argument("--seed", type=int, default=42)
    p1.add_argument("--min-body", type=int, default=800)
    p1.add_argument("--window-chars", type=int, default=4000)
    p1.add_argument("--out", default="sample.tsv")
    p1.add_argument("--window-dir", default="sample_windows")
    p1.set_defaults(fn=_cmd_sample)
    p3 = sub.add_parser("kappa")
    p3.add_argument("gold_a")
    p3.add_argument("gold_b")
    p3.set_defaults(fn=_cmd_kappa)
    p4 = sub.add_parser("stats")
    p4.add_argument("classified")
    p4.set_defaults(fn=_cmd_stats)
    args = ap.parse_args(argv)
    return args.fn(args)
