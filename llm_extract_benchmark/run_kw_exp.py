import json, os, re, sys, time, urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODELS = ["qwen3.5:9b", "qwen3:8b", "qwen2.5:latest"]
METHODS = ["oneshot", "fewshot"]
PROMPT_INTRO = (
    "You are extracting structured information from a verbatim transcript of an Internet Governance Forum (IGF) meeting.\n"
    "Read the excerpt below and extract the 8 to 15 most important keywords and key phrases.\n"
    "Rules: phrases must be short (1-4 words); prefer phrases that appear verbatim in the text; cover topics, issues, actors and outcomes; do not invent.\n"
    "Return ONLY strict JSON with this shape:\n{\"keywords\": [\"kw1\", \"kw2\", \"...\"]}\n\nExcerpt:\n")
EXAMPLE_IN = (
    "Example:\nExcerpt: \"IGF 2 Rio de Janeiro, Brazil 13 November 2007 Access >>HELIO COSTA: What makes the IGF a different forum is the fact that here, the forum is open to all. "
    "Even though there have been significant efforts by governments and companies to reduce the digital gap, differences still persist in access to information between developed and developing countries "
    "and between the rich and the poor. We are here to try and find solutions for the infrastructure, legal, and regulatory bottlenecks.\"\n"
    'Example output:\n{"keywords": ["access", "digital gap", "developed and developing countries", "rich and the poor", "infrastructure bottlenecks", "regulatory bottlenecks", "open to all"]}\n\n')

def extract_window(entry, base_dir):
    with open(os.path.join(base_dir, entry["file"]), "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<").replace("&quot;", '"')
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: entry["window_chars"]]

def build_prompt(method, window):
    if method == "fewshot":
        return PROMPT_INTRO.replace("Excerpt:\n", EXAMPLE_IN + "Now do the same for this excerpt:\n") + window
    return PROMPT_INTRO + window

def call_ollama(model, prompt, timeout=180):
    options = {"temperature": 0, "num_predict": 2000}
    if model.startswith("qwen3"):
        options["think"] = False
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "format": "json", "options": options}).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/generate", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def main():
    gold_path = sys.argv[1] if len(sys.argv) > 1 else "gold_keywords.json"
    base_dir = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\guhao\PyCharmMiscProject\igf_classified_20260812_060303\_invalid\other"
    out_dir = "results_kw"
    os.makedirs(out_dir, exist_ok=True)
    with open(gold_path, "r", encoding="utf-8-sig") as f:
        gold = json.load(f)
    runs = []
    for model in MODELS:
        for method in METHODS:
            for entry in gold:
                window = extract_window(entry, base_dir)
                prompt = build_prompt(method, window)
                t0 = time.time()
                try:
                    resp = call_ollama(model, prompt)
                    raw = resp.get("response", "")
                    source = "response"
                    if not raw.strip() and resp.get("thinking"):
                        raw = resp.get("thinking", "")
                        source = "thinking_salvage"
                        matches = re.findall(r'\{\s*"keywords"\s*:\s*\[[^\]]*\]\s*\}', raw, re.S)
                        raw = matches[-1] if matches else ""
                    try:
                        obj = json.loads(raw)
                        kws = obj.get("keywords", [])
                        if isinstance(kws, str):
                            kw = [x.strip() for x in re.split(r"[,\n;]", kws) if x.strip()]
                        else:
                            kw = [str(x) for x in kws]
                        parsed = bool(kw)
                    except Exception:
                        m = re.search(r"\[.*\]", raw, re.S)
                        kw = [str(x).strip('"') for x in re.findall(r'"([^"]+)"', m.group(0))] if m else []
                        parsed = bool(kw)
                    runs.append({"model": model, "method": method, "doc": entry["doc"],
                                 "keywords": kw, "parsed": parsed, "source": source,
                                 "latency_s": round(time.time() - t0, 1),
                                 "eval_count": resp.get("eval_count")})
                    print("[%s/%s] %-16s %-8s n=%d latency=%.1fs" % (model, method, entry["doc"], "ok" if parsed else "fail", len(kw), time.time() - t0), flush=True)
                except Exception as e:
                    runs.append({"model": model, "method": method, "doc": entry["doc"],
                                 "keywords": [], "parsed": False,
                                 "latency_s": round(time.time() - t0, 1), "error": str(e)[:200]})
                    print("[%s/%s] %-16s ERROR %s" % (model, method, entry["doc"], str(e)[:120]), flush=True)
                time.sleep(1.0)
    out_path = os.path.join(out_dir, "kw_raw_results.json")
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        keys = set((r.get("model"), r.get("method")) for r in runs)
        data["runs"] = [r for r in data.get("runs", []) if (r.get("model"), r.get("method")) not in keys] + runs
    else:
        data = {"gold": gold_path, "base_dir": base_dir, "runs": runs}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("saved", out_path)

if __name__ == "__main__":
    main()
