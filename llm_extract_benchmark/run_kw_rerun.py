import json
import os
import re
import sys
import time
import urllib.request

Q = chr(34)
HERE = os.path.dirname(os.path.abspath(__file__))
OLLAMA = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')
DEFAULT_GOLD = os.path.join(HERE, 'gold_keywords.json')
DEFAULT_DOCS = os.path.join(HERE, "sample_windows")
OUT = os.path.join(HERE, 'results_kw', 'kw_raw_results.json')

PROMPT_INTRO = (
    'You are extracting structured information from a verbatim transcript of an Internet Governance Forum (IGF) meeting.\n'
    'Read the excerpt below and extract the 8 to 15 most important keywords and key phrases.\n'
    'Rules: phrases must be short (1-4 words); prefer phrases that appear verbatim in the text; '
    'cover topics, issues, actors and outcomes; do not invent.\n'
    'Return ONLY strict JSON with this shape:\n'
    '{' + Q + 'keywords' + Q + ': [' + Q + 'kw1' + Q + ', ' + Q + 'kw2' + Q + ', ' + Q + '...' + Q + ']}\n\n'
    'Excerpt:\n'
)
EXAMPLE = (
    'Example:\nExcerpt: ' + Q + 'IGF 2 Rio de Janeiro, Brazil 13 November 2007 Access >>HELIO COSTA: '
    'What makes the IGF a different forum is the fact that here, the forum is open to all.' + Q + '\n'
    'Example output:\n{' + Q + 'keywords' + Q + ': [' + Q + 'access' + Q + ', ' + Q + 'digital gap' + Q + ', '
    + Q + 'developed and developing countries' + Q + ']}\n\n'
    'Now do the same for this excerpt:\n'
)

def extract_window(entry, doc_dir):
    with open(os.path.join(doc_dir, entry['file']), 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    text = re.sub(r'(?is)<script.*?</script>', ' ', html)
    text = re.sub(r'(?is)<style.*?</style>', ' ', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<').replace('&quot;', Q)
    text = re.sub(r'&#\d+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[: int(entry.get('window_chars', 4000))]

def call_ollama(model, prompt, timeout=240):
    options = {'temperature': 0, 'num_predict': 2000}
    if model.startswith('qwen3'):
        options['think'] = False
    payload = json.dumps({'model': model, 'prompt': prompt, 'stream': False,
                          'format': 'json', 'options': options}).encode('utf-8')
    req = urllib.request.Request(OLLAMA + '/api/generate', data=payload,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def parse_keywords(resp):
    raw = resp.get('response', '')
    source = 'response'
    if not raw.strip() and resp.get('thinking'):
        raw = resp.get('thinking', '')
        source = 'thinking_salvage'
        pat = r'\{\s*' + Q + 'keywords' + Q + r'\s*:\s*\[[^\]]*\]\s*\}'
        matches = re.findall(pat, raw, re.S)
        raw = matches[-1] if matches else ''
    try:
        obj = json.loads(raw)
        kws = obj.get('keywords', [])
        if isinstance(kws, str):
            kw = [x.strip() for x in re.split(r'[,\n;]', kws) if x.strip()]
        else:
            kw = [str(x).strip() for x in kws if str(x).strip()]
        return [x for x in kw if x], source, raw
    except Exception:
        m = re.search(r'\[.*\]', raw, re.S)
        kw = []
        if m:
            pat2 = Q + '([^' + Q + ']+)' + Q
            kw = [x.strip() for x in re.findall(pat2, m.group(0))]
        return kw, source + '+fallback', raw

def main():
    model = sys.argv[1]
    methods = sys.argv[2].split(',') if len(sys.argv) > 2 else ['oneshot']
    gold_path = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_GOLD
    doc_dir = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_DOCS
    with open(gold_path, 'r', encoding='utf-8-sig') as f:
        gold = json.load(f)
    new_runs = []
    for method in methods:
        for entry in gold:
            window = extract_window(entry, doc_dir)
            prompt = (PROMPT_INTRO + EXAMPLE + window) if method == 'fewshot' else (PROMPT_INTRO + window)
            t0 = time.time()
            try:
                resp = call_ollama(model, prompt)
                kw, source, raw = parse_keywords(resp)
                new_runs.append({'model': model, 'method': method, 'doc': entry['doc'],
                                 'keywords': kw, 'parsed': bool(kw), 'source': source,
                                 'latency_s': round(time.time() - t0, 1),
                                 'raw': raw[:2000]})
                print('[%s/%s] %-24s n=%d src=%s %.1fs' % (model, method, entry['doc'], len(kw), source, time.time() - t0), flush=True)
            except Exception as exc:
                new_runs.append({'model': model, 'method': method, 'doc': entry['doc'],
                                 'keywords': [], 'parsed': False, 'source': 'error',
                                 'latency_s': round(time.time() - t0, 1), 'error': str(exc)[:200]})
                print('[%s/%s] %-24s ERROR %s' % (model, method, entry['doc'], str(exc)[:120]), flush=True)
            time.sleep(1.0)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT):
        with open(OUT, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        keys = set((r.get('model'), r.get('method')) for r in new_runs)
        data['runs'] = [r for r in data.get('runs', []) if (r.get('model'), r.get('method')) not in keys] + new_runs
    else:
        data = {'gold': gold_path, 'base_dir': doc_dir, 'runs': new_runs}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('merged %d runs for %s into %s' % (len(new_runs), model, OUT))

if __name__ == '__main__':
    main()
