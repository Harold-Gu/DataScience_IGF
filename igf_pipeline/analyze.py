import argparse
import csv
import html
import json
import math
import os
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import mean, median

from bs4 import BeautifulSoup

from . import crawl


MEETING_TYPES = ['workshop', 'open-forum', 'lightning-talk', 'day-0-event',
                 'launch-award', 'networking', 'main-session', 'town-hall',
                 'transcript', 'report', 'schedule', 'participants',
                 'dc-bpf-nri', 'other']

ENTITY_FIELD_RE = re.compile(r'speaker|organi[sz]er|panelist|moderator|rapporteur|proposer', re.I)
SDG_RE = re.compile(r'GOAL\s*(\d{1,2})', re.I)
ENTITY_JUNK_RE = re.compile(
    r':\s*$|^speaker\s*\d+$|^format|^duration|^round table|^panel\s*[-–]|^theater$|'
    r'^classroom$|^other\s*[-–]|^u-shape$|^circle$|^main floor$|'
    r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$|@|^https?://|'
    r'\[email protected\]|'
    r'^break-out group|^debate\s*[-–]|^roundtable$|^speakers?$|^moderators?$|'
    r'^agenda$|^online moderator$|^onsite moderator$', re.I)
ORG_FIELD_KEYS = ['report', 'rapporteur', 'policy_questions', 'speakers', 'sdgs',
                  'key_session_takeaways', 'call_to_action', 'gender_issues',
                  'diversity', 'interventions', 'agenda', 'language', 'room', 'time']

TOPICS = {
    'surveillance': r'\bsurveillance\b|\bmass monitoring\b|\bnsa\b|\bsnowden\b',
    'privacy-data-protection': r'\bprivacy\b|\bdata protection\b|\bgdpr\b|personal data',
    'AI': r'\bartificial intelligence\b|\bai\b|\bmachine learning\b|\bllm\b|generative ai',
    'cybersecurity': r'\bcybersecurity\b|\bcyber security\b|\bcybercrime\b|\bcyber attacks?\b|\bransomware\b',
    'disinformation': r'\bdisinformation\b|\bmisinformation\b|\bfake news\b|\binformation integrity\b',
    'digital-inclusion': r'\bdigital divide\b|\bdigital inclusion\b|\bconnectivity\b|\bbroadband\b|\baccessibility\b',
    'gender': r'\bgender\b|\bwomen\b|\bfeminist\b|\bgirls\b',
    'blockchain': r'\bblockchain\b|\bcryptocurrenc\w*\b|\bbitcoin\b|\bweb3\b',
    'DNS-infrastructure': r'\bdns\b|\broot server\b|\bdomain name\b|\bicann\b',
    'AI-ethics-governance': r'\bai governance\b|\bai ethics\b|\bresponsible ai\b',
    'covid': r'\bcovid\b|\bpandemic\b|\bcoronavirus\b',
    'human-rights': r'\bhuman rights\b|\bfreedom of expression\b|\bcensorship\b',
    'quantum': r'\bquantum\b',
    'metaverse': r'\bmetaverse\b|\bimmersive\b',
    'content-moderation': r'\bcontent moderation\b|\bhate speech\b|\bharmful content\b',
    '5G-6G': r'\b5g\b|\b6g\b',
    'sovereignty': r'\bsovereignty\b|\bdata localization\b|\bgeopolitic',
    'capacity-building': r'\bcapacity building\b|\bdigital skills\b|\bliteracy\b',
}


def log(*args):
    print(*args)


def field_text(record, key_re):
    fields = record.get('drupal_fields') or {}
    if not isinstance(fields, dict):
        return ''
    chunks = []
    for name, value in fields.items():
        if not key_re.search(str(name)):
            continue
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, dict):
            content = value.get('content') or []
            for item in content:
                if isinstance(item, dict):
                    chunks.append(str(item.get('text') or ''))
                else:
                    chunks.append(str(item))
        else:
            chunks.append(str(value))
    return '\n'.join(c for c in chunks if c.strip())


def write_csv(out_dir, name, header, rows):
    path = out_dir / name
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def ascii_bar(value, maximum, width=30):
    if maximum <= 0:
        return ''
    filled = min(width, int(round(width * value / maximum)))
    return '#' * filled + '.' * (width - filled)


def analysis_main(argv=None):
    import networkx as nx
    from sklearn.feature_extraction.text import TfidfVectorizer

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Deep-dive IGF corpus analysis')
    parser.add_argument('--input', help='path to denoised all.json (default: newest igf_denoised_*/all.json)')
    parser.add_argument('--output', help='output directory (default: igf_analysis_<timestamp>)')
    parser.add_argument('--top-k', type=int, default=15, help='keywords per year (default 15)')
    args = parser.parse_args(argv)

    if args.input:
        input_path = Path(args.input).resolve()
    else:
        candidates = sorted(Path.cwd().glob('igf_denoised_*/all.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            sys.exit('no igf_denoised_*/all.json found; use --input')
        input_path = candidates[0]
    log('INPUT :', input_path)
    records = json.load(open(input_path, encoding='utf-8'))
    log('Records:', len(records))

    out_dir = Path(args.output) if args.output else Path.cwd() / ('igf_analysis_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []

    # --- type x year matrix -------------------------------------------------
    years = sorted({r.get('year') for r in records if isinstance(r.get('year'), int)})
    matrix = defaultdict(Counter)
    for r in records:
        rec_type = r.get('type') or 'other'
        rec_year = r.get('year')
        matrix[rec_type][rec_year if isinstance(rec_year, int) else 'unknown'] += 1
    header = ['type'] + [str(y) for y in years] + ['unknown']
    rows = []
    for rec_type in MEETING_TYPES:
        if rec_type not in matrix:
            continue
        rows.append([rec_type] + [matrix[rec_type].get(y, 0) for y in years]
                    + [matrix[rec_type].get('unknown', 0)])
    write_csv(out_dir, 'type_year_matrix.csv', header, rows)
    log('\n== TYPE x YEAR (console) ==')
    print('%-16s' % 'type', ' '.join('%5s' % y for y in years), '%6s' % 'unk')
    for row in rows:
        print('%-16s' % row[0], ' '.join('%5d' % v for v in row[1:-1]), '%6d' % row[-1])

    # HTML heatmap (no matplotlib needed)
    max_cell = max((max(row[1:]) for row in rows), default=1)
    table_rows = []
    for row in rows:
        cells = ''.join(
            '<td style="background:rgba(31,119,180,%.3f);color:#fff;text-align:center">%s</td>'
            % (0.15 + 0.85 * v / max_cell, v if v else '') for v in row[1:])
        table_rows.append('<tr><th>%s</th>%s</tr>' % (html.escape(row[0]), cells))
    heatmap_html = ('<!doctype html><html><head><meta charset="utf-8"><title>IGF type x year</title></head>'
                    '<body><h2>IGF corpus: records by type and year</h2><table border="1" cellspacing="0" cellpadding="4">'
                    '<tr><th>type</th>%s<th>unknown</th></tr>%s</table></body></html>'
                    % (''.join('<th>%s</th>' % y for y in years), ''.join(table_rows)))
    (out_dir / 'type_year_heatmap.html').write_text(heatmap_html, encoding='utf-8')
    report.append('type x year matrix -> type_year_matrix.csv, type_year_heatmap.html')

    # --- body length stats --------------------------------------------------
    body_rows = [['type', 'records', 'total_chars', 'mean_chars', 'median_chars', 'max_chars', 'empty']]
    lengths = defaultdict(list)
    for r in records:
        lengths[r.get('type') or 'other'].append(len((r.get('body_text') or '').strip()))
    log('\n== BODY LENGTH BY TYPE ==')
    body_types = MEETING_TYPES + sorted(t for t in lengths if t not in MEETING_TYPES)
    for rec_type in body_types:
        vals = lengths.get(rec_type)
        if not vals:
            continue
        body_rows.append([rec_type, len(vals), sum(vals), int(mean(vals)), int(median(vals)), max(vals), sum(1 for v in vals if v == 0)])
        print('%-16s n=%-5d mean=%-7d median=%-6d max=%-8d empty=%d' % (
            rec_type, len(vals), mean(vals), median(vals), max(vals), sum(1 for v in vals if v == 0)))
    write_csv(out_dir, 'body_stats.csv', body_rows[0], body_rows[1:])
    report.append('body length stats -> body_stats.csv')

    # --- yearly TF-IDF keywords --------------------------------------------
    year_docs = defaultdict(list)
    for r in records:
        year = r.get('year')
        if isinstance(year, int) and (r.get('body_text') or '').strip():
            year_docs[year].append(r.get('body_text'))
    tfidf_rows = [['year', 'rank', 'term', 'score']]
    keyword_table = []
    log('\n== YEARLY KEYWORDS (TF-IDF, top %d) ==' % args.top_k)
    for year in sorted(year_docs):
        docs = year_docs[year]
        if len(docs) < 20:
            continue
        vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2), max_features=6000, sublinear_tf=True)
        try:
            matrix_tf = vectorizer.fit_transform(docs)
        except ValueError:
            continue
        scores = matrix_tf.mean(axis=0).A1
        top_idx = scores.argsort()[::-1][:args.top_k]
        terms = vectorizer.get_feature_names_out()
        line = '  %d:' % year
        for rank, idx in enumerate(top_idx, 1):
            if scores[idx] <= 0:
                break
            tfidf_rows.append([year, rank, terms[idx], round(float(scores[idx]), 6)])
            line += ' %s(%.3f)' % (terms[idx], scores[idx])
        keyword_table.append(line)
        print(line)
    write_csv(out_dir, 'yearly_keywords.csv', tfidf_rows[0], tfidf_rows[1:])
    report.append('yearly TF-IDF keywords -> yearly_keywords.csv')

    # --- topic drift --------------------------------------------------------
    topic_re = {name: re.compile(pattern, re.I) for name, pattern in TOPICS.items()}
    per_year = defaultdict(lambda: defaultdict(int))
    chars_per_year = Counter()
    for r in records:
        year = r.get('year')
        if not isinstance(year, int):
            continue
        body = r.get('body_text') or ''
        chars_per_year[year] += len(body)
        for name, regex in topic_re.items():
            per_year[year][name] += len(regex.findall(body))
    drift_rows = [['year'] + list(TOPICS.keys()) + ['body_chars']]
    log('\n== TOPIC DRIFT (hits per 100k body chars) ==')
    print('%-6s' % 'year', '  '.join('%-26s' % name[:26] for name in TOPICS))
    for year in sorted(per_year):
        base = max(chars_per_year[year], 1)
        norm = {name: 100000.0 * per_year[year][name] / base for name in TOPICS}
        drift_rows.append([year] + [round(norm[n], 1) for n in TOPICS] + [chars_per_year[year]])
        print('%-6d' % year, '  '.join('%6.1f %-18s' % (norm[n], ascii_bar(norm[n], 200, 18)) for n in TOPICS))
    write_csv(out_dir, 'topic_drift.csv', drift_rows[0], drift_rows[1:])
    report.append('topic drift -> topic_drift.csv')

    # --- SDG distribution ---------------------------------------------------
    sdg_counter = Counter()
    sdg_years = defaultdict(Counter)
    for r in records:
        text = field_text(r, re.compile(r'sdg', re.I))
        found = set(int(m) for m in SDG_RE.findall(text))
        for goal in found:
            sdg_counter[goal] += 1
            if isinstance(r.get('year'), int):
                sdg_years[r.get('year')][goal] += 1
    log('\n== SDG MENTIONS (from Drupal "GOAL n" field) ==')
    for goal, count in sorted(sdg_counter.items()):
        print('  SDG %-3d %d' % (goal, count))
    write_csv(out_dir, 'sdg_counts.csv', ['sdg', 'records'],
              [[goal, count] for goal, count in sorted(sdg_counter.items())])
    report.append('SDG counts -> sdg_counts.csv')

    # --- field coverage by type --------------------------------------------
    field_re = {key: re.compile(re.escape(key), re.I) for key in ORG_FIELD_KEYS}
    coverage_rows = [['type', 'records'] + ORG_FIELD_KEYS]
    log('\n== FIELD COVERAGE BY TYPE (fraction of pages carrying the field) ==')
    print('%-16s %6s' % ('type', 'n'), ' '.join('%-11s' % k[:11] for k in ORG_FIELD_KEYS))
    cover_types = MEETING_TYPES + sorted(
        t for t in {r.get('type') or 'other' for r in records} if t not in MEETING_TYPES)
    for rec_type in cover_types:
        subset = [r for r in records if (r.get('type') or 'other') == rec_type]
        if not subset:
            continue
        fracs = []
        for key in ORG_FIELD_KEYS:
            fracs.append(sum(1 for r in subset if field_text(r, field_re[key]).strip()) / len(subset))
        coverage_rows.append([rec_type, len(subset)] + [round(f, 3) for f in fracs])
        print('%-16s %6d' % (rec_type, len(subset)), ' '.join('%8.2f   ' % f for f in fracs))
    write_csv(out_dir, 'field_coverage.csv', coverage_rows[0], coverage_rows[1:])
    report.append('field coverage -> field_coverage.csv')

    # --- organization co-occurrence network --------------------------------
    entity_docs = defaultdict(set)
    entity_pairs = Counter()
    for r in records:
        text = field_text(r, ENTITY_FIELD_RE)
        entities = set()
        for line in text.splitlines():
            line = re.sub(r'^\s*[-*\d.)]+\s*', '', line).strip()
            line = re.sub(r'^[,;:]\s*', '', line).strip()
            line = re.sub(r'\s+', ' ', line)
            if 2 < len(line) <= 80 and not line.isdigit() and not ENTITY_JUNK_RE.search(line):
                entities.add(line)
        for entity in entities:
            entity_docs[entity].add(r.get('rel_path'))
        for pair in combinations(sorted(entities), 2):
            entity_pairs[pair] += 1
    graph = nx.Graph()
    for entity, docs in entity_docs.items():
        graph.add_node(entity, docs=len(docs), weight=0)
    for (left, right), weight in entity_pairs.items():
        if graph.has_edge(left, right):
            graph[left][right]['weight'] += weight
        else:
            graph.add_edge(left, right, weight=weight)
    degree = dict(graph.degree(weight='weight'))
    nx.set_node_attributes(graph, degree, 'weighted_degree')
    nx.write_gexf(graph, out_dir / 'org_network.gexf')
    node_rows = [['entity', 'docs', 'weighted_degree', 'neighbors']]
    for entity, _ in sorted(degree.items(), key=lambda kv: -kv[1])[:200]:
        node_rows.append([entity, graph.nodes[entity]['docs'], degree[entity], graph.degree(entity)])
    write_csv(out_dir, 'org_network_nodes.csv', node_rows[0], node_rows[1:])
    log('\n== TOP ORGANIZATIONS / PEOPLE (co-occurrence network) ==')
    for entity, weight in sorted(degree.items(), key=lambda kv: -kv[1])[:30]:
        print('  %-55s docs=%-3d deg=%.1f' % (entity[:55], graph.nodes[entity]['docs'], weight))
    report.append('org co-occurrence network -> org_network.gexf, org_network_nodes.csv')

    # --- link domain stats --------------------------------------------------
    domain_counter = Counter()
    external_counter = Counter()
    total_links = 0
    for r in records:
        for link in r.get('links') or []:
            href = (link or {}).get('href') if isinstance(link, dict) else str(link)
            if not href or not re.match(r'^https?://', href):
                continue
            total_links += 1
            host = (urllib.parse.urlsplit(href).hostname or '').lower()
            if host.startswith('www.'):
                host = host[4:]
            domain_counter[host] += 1
            if 'intgovforum.org' not in host and host:
                external_counter[host] += 1
    internal = domain_counter.get('intgovforum.org', 0)
    log('\n== LINK DOMAINS (total=%d, internal=%d, external=%d) ==' % (
        total_links, internal, total_links - internal))
    for domain, count in external_counter.most_common(20):
        print('  %-40s %d' % (domain[:40], count))
    write_csv(out_dir, 'link_domains.csv', ['domain', 'links'],
              [[d, c] for d, c in domain_counter.most_common(100)])
    report.append('link domains -> link_domains.csv')

    # --- duplicates ---------------------------------------------------------
    hashes = Counter(r.get('content_hash') for r in records if r.get('content_hash'))
    dups = {h: c for h, c in hashes.items() if c > 1}
    log('\n== NEAR-DUPLICATES (content_hash) ==')
    log('  duplicate hash groups: %d, affected records: %d' % (len(dups), sum(dups.values())))
    report.append('duplicates: %d hash groups with >1 record' % len(dups))

    # --- report file --------------------------------------------------------
    with open(out_dir / 'analysis_report.txt', 'w', encoding='utf-8') as handle:
        handle.write('IGF corpus analysis %s\ninput: %s\nrecords: %d\n\n' % (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), input_path, len(records)))
        handle.write('\n'.join(report))
        handle.write('\n\nyearly keywords:\n')
        handle.write('\n'.join(keyword_table))
        handle.write('\n')
    log('\nDone -> %s' % out_dir)
    return 0


STOPWORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "english_stopwords.txt")


def _load_stopwords():
    # Glasgow SMART English stop list (318 entries), as bundled in scikit-learn.
    try:
        with open(STOPWORDS_PATH, encoding="utf-8") as fh:
            words = {line.strip().lower() for line in fh if line.strip()}
        if words:
            return words
    except OSError:
        pass
    try:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
        return set(ENGLISH_STOP_WORDS)
    except Exception:
        pass
    return set("the a an and or but of to in on for with as at by is are was "
               "were be been this that these those it its from we you they he "
               "she i not no so".split())


STOPWORDS = _load_stopwords()

_PUNCT = re.compile(r"[^A-Za-z0-9' -]+")
_SPACE = re.compile(r"\s+")


def _norm_text(text):
    text = _PUNCT.sub(" ", str(text or "").lower())
    return _SPACE.sub(" ", text).strip()


def _phrase_ngrams(words, max_n=4):
    out = []
    for n in range(1, max_n + 1):
        for i in range(len(words) - n + 1):
            out.append(words[i:i + n])
    return out


def _is_stop_phrase(ws):
    return all(w in STOPWORDS or len(w) <= 1 for w in ws)


def _score_keywords(gold_kws, pred_kws):
    def norm(x):
        return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9' -]", " ", str(x).lower())).strip(" -'")

    def f1(a, b):
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        inter = len(a & b)
        p = inter / len(a)
        r = inter / len(b)
        return 2 * p * r / (p + r) if (p + r) else 0.0

    g = {norm(k.get("kw", k) if isinstance(k, dict) else k) for k in gold_kws}
    p = {norm(k) for k in pred_kws}
    g = {x for x in g if x}
    p = {x for x in p if x}
    if not p or not g:
        return {"phrase_f1": 0.0, "token_f1": 0.0, "soft_recall": 0.0,
                "soft_precision": 0.0, "exact_hit_rate": 0.0}
    phrase_f1 = f1(g, p)
    tg = set()
    for x in g:
        tg.update(x.split())
    tp = set()
    for x in p:
        tp.update(x.split())
    token_f1 = f1(tg, tp)

    def soft(x, others):
        xt = x.split()
        for o in others:
            ot = o.split()
            if x == o:
                return 1.0
            if len(xt) >= 2 and len(ot) >= 2:
                if xt[0] == ot[0] and xt[-1] == ot[-1]:
                    return 0.7
                if set(xt) & set(ot):
                    return 0.5
            if len(xt) == 1 and ot and xt[0] in ot:
                return 0.5
        return 0.0

    sr = sum(soft(x, p) for x in g) / len(g)
    sp = sum(soft(x, g) for x in p) / len(p)
    exact = sum(1 for x in g if x in p) / len(g)
    return {"phrase_f1": round(phrase_f1, 4), "token_f1": round(token_f1, 4),
            "soft_recall": round(sr, 4), "soft_precision": round(sp, 4),
            "exact_hit_rate": round(exact, 4)}


def _candidate_phrases(text, max_n=4):
    sentences = re.split(r"[.!?;\n]+", text)
    phrases = []
    for sent in sentences:
        ws = _norm_text(sent).split()
        cur = []
        for w in ws:
            if w in STOPWORDS:
                if len(cur) >= 2:
                    phrases.append(" ".join(cur))
                cur = []
            else:
                cur.append(w)
        if len(cur) >= 2:
            phrases.append(" ".join(cur))
    seen = set()
    out = []
    for ph in phrases:
        if ph and ph not in seen:
            seen.add(ph)
            out.append(ph)
    return out


def rake_keywords(text, topn=12):
    phrases = _candidate_phrases(text)
    freq = {}
    deg = {}
    for ph in phrases:
        for w in ph.split():
            freq[w] = freq.get(w, 0) + 1
            deg[w] = deg.get(w, 0) + len(ph.split()) - 1
    scores = {}
    for ph in phrases:
        ws = ph.split()
        scores[ph] = sum((deg.get(w, 0) + freq.get(w, 0)) for w in ws) / max(1, len(ws))
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [ph for ph, _ in ranked[:topn]]


def textrank_keywords(text, topn=12, window=3, iters=60, damp=0.85):
    words = _norm_text(text).split()
    if len(words) < 5:
        return rake_keywords(text, topn)
    graph = {}
    for i, w in enumerate(words):
        if w in STOPWORDS or len(w) <= 1:
            continue
        graph.setdefault(w, set())
        for j in range(i + 1, min(i + window + 1, len(words))):
            w2 = words[j]
            if w2 in STOPWORDS or len(w2) <= 1:
                continue
            graph[w].add(w2)
            graph.setdefault(w2, set()).add(w)
    if not graph:
        return rake_keywords(text, topn)
    score = {w: 1.0 / len(graph) for w in graph}
    for _ in range(iters):
        nxt = {}
        for w, neigh in graph.items():
            nxt[w] = (1 - damp) / len(graph) + damp * sum(
                score[n] / max(1, len(graph.get(n, ()))) for n in neigh)
        score = nxt
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:topn]]


def _builtin_keybert(text, topn=12):
    words = _norm_text(text).split()
    if len(words) < 5:
        return rake_keywords(text, topn)
    cands = [" ".join(ng) for ng in _phrase_ngrams(words, 4)]
    cands = [c for c in cands if not _is_stop_phrase(c.split())]
    cands = list(dict.fromkeys(cands))
    if not cands:
        return rake_keywords(text, topn)
    vocab = sorted({w for c in cands for w in c.split()})
    docfreq = {}
    for c in cands:
        for w in set(c.split()):
            docfreq[w] = docfreq.get(w, 0) + 1
    idf = {w: math.log((1 + len(cands)) / (1 + docfreq[w])) + 1 for w in vocab}
    def vec(c):
        v = [0.0] * len(vocab)
        for w in c.split():
            v[vocab.index(w)] += idf[w]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))
    vectors = {c: vec(c) for c in cands}
    chosen = []
    remaining = list(cands)
    while remaining and len(chosen) < topn:
        remaining.sort(key=lambda c: (-cos(vectors[c], [1.0] * len(vocab)), c))
        best = remaining[0]
        chosen.append(best)
        remaining = [c for c in remaining[1:] if
                     cos(vectors[c], vectors[best]) < 0.85]
    return chosen


def keybert_keywords(text, topn=12):
    try:
        from keybert import KeyBERT as _KeyBERT
    except Exception:
        _KeyBERT = None
    if _KeyBERT is not None:
        try:
            model = _KeyBERT()
            return [kw for kw, _ in model.extract_keywords(
                str(text), top_n=topn, keyphrase_ngram_range=(1, 4),
                stop_words="english", use_mmr=True, diversity=0.4)]
        except Exception:
            pass
    return _builtin_keybert(text, topn)


BASELINES = {
    "rake": rake_keywords,
    "textrank": textrank_keywords,
    "keybert": keybert_keywords,
}


_WINDOW_INDEX_CACHE = {}


def _resolve_gold_html(gold_entry, base_dir):
    rel = str(gold_entry.get("rel_path") or "")
    if rel:
        cand = os.path.join(base_dir, rel.replace("/", os.sep))
        if os.path.exists(cand):
            return cand
    fname = str(gold_entry.get("file") or "")
    cand = os.path.join(base_dir, fname)
    if os.path.exists(cand):
        return cand
    idx = _WINDOW_INDEX_CACHE.get(base_dir)
    if idx is None:
        idx = {}
        for root, _dirs, files in os.walk(base_dir):
            for fn in files:
                if fn.lower().endswith((".html", ".htm")):
                    idx.setdefault(fn.lower(), os.path.join(root, fn))
        _WINDOW_INDEX_CACHE[base_dir] = idx
    return idx.get(fname.lower(), "")


def _extract_window(gold_entry, base_dir):
    fpath = _resolve_gold_html(gold_entry, base_dir)
    if not fpath or not os.path.exists(fpath):
        return ""
    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&amp;", "&"), ("&gt;", ">"), ("&lt;", "<"),
                 ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:gold_entry.get("window_chars", 4000)]


def run_baselines(gold_path, base_dir, out_dir=None, topn=12, seed=1):
    with open(gold_path, "r", encoding="utf-8-sig") as f:
        gold = json.load(f)
    gold = gold if isinstance(gold, list) else gold.get("docs", [])
    if not gold:
        print("[ERR] no gold documents found")
        return 2
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(gold_path)), "results_kw")
    os.makedirs(out_dir, exist_ok=True)
    runs = []
    metric_rows = []
    report_lines = ["Baseline keyword extraction vs gold", ""]
    sums = {name: {m: 0.0 for m in ("phrase_f1", "token_f1", "soft_recall",
                                    "soft_precision", "exact_hit_rate")}
            for name in BASELINES}
    n_win = 0
    for g in gold:
        win = _extract_window(g, base_dir)
        if not win:
            continue
        n_win += 1
        gk = g.get("keywords") or []
        for name, fn in BASELINES.items():
            pred = fn(win, topn=topn)
            metrics = _score_keywords(gk, pred)
            for m, v in metrics.items():
                sums[name][m] += v
            runs.append({"model": name, "method": "baseline", "doc": g.get("doc"),
                         "keywords": pred, "parsed": True, "source": "baseline",
                         "latency_s": None, "error": None, "metrics": metrics})
            metric_rows.append([g.get("doc"), name, topn] +
                               [metrics[m] for m in
                                ("phrase_f1", "token_f1", "soft_recall",
                                 "soft_precision", "exact_hit_rate")])
    report_lines.append(f"n_docs_with_window={n_win} topn={topn}")
    report_lines.append("")
    report_lines.append("baseline   phrase_f1  token_f1  soft_recall  soft_precision  exact_hit")
    for name in BASELINES:
        mean = {m: round(sums[name][m] / max(1, n_win), 4) for m in sums[name]}
        report_lines.append(f"{name:9s} {mean['phrase_f1']:.4f}     {mean['token_f1']:.4f}  "
                            f"{mean['soft_recall']:.4f}      {mean['soft_precision']:.4f}       "
                            f"{mean['exact_hit_rate']:.4f}")
    runs_path = os.path.join(out_dir, "baseline_runs.json")
    with open(runs_path, "w", encoding="utf-8") as f:
        json.dump({"gold": os.path.basename(gold_path), "base_dir": base_dir,
                   "runs": runs}, f, ensure_ascii=False, indent=1)
    csv_path = os.path.join(out_dir, "baseline_metrics.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "baseline", "topn", "phrase_f1", "token_f1",
                    "soft_recall", "soft_precision", "exact_hit_rate"])
        w.writerows(metric_rows)
    report_path = os.path.join(out_dir, "baseline_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    print("\n".join(report_lines))
    print(f"\nwrote: {runs_path}")
    print(f"wrote: {csv_path}")
    print(f"wrote: {report_path}")
    return 0



def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _palette(t, vmax=1.0):
    t = max(0.0, min(1.0, t / max(vmax, 1e-9)))
    r = int(255)
    g = int(255 * (1 - t) * 0.75)
    b = int(255 * (1 - t))
    return "#%02x%02x%02x" % (r, g, b)


def line_svg(series, out_path, title, xlabel="", ylabel="", width=920, height=430):
    labels = sorted({x for pts in series.values() for x, _ in pts})
    vals = [y for pts in series.values() for _, y in pts if y is not None]
    vmin, vmax = (min(vals), max(vals)) if vals else (0, 1)
    if vmax == vmin:
        vmax = vmin + 1
    ml, mr, mt, mb = 70, 20, 30, 55
    pw, ph = width - ml - mr, height - mt - mb
    parts = []
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height))
    parts.append('<rect width="100%%" height="100%%" fill="white"/>')
    parts.append('<text x="%d" y="20" font-size="14" font-family="sans-serif">%s</text>' % (ml, _esc(title)))
    for i in range(6):
        y = mt + ph - ph * i / 5
        v = vmin + (vmax - vmin) * i / 5
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#ddd"/>' % (ml, y, ml + pw, y))
        parts.append('<text x="%d" y="%d" font-size="10" fill="#666" text-anchor="end">%.2f</text>' % (ml - 6, y + 3, v))
    step = max(1, len(labels) // 12)
    for i, x in enumerate(labels):
        px = ml + pw * i / max(1, len(labels) - 1)
        if i % step == 0:
            parts.append('<text x="%d" y="%d" font-size="10" fill="#666" text-anchor="middle">%s</text>' % (px, height - mb + 15, _esc(str(x))))
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf"]
    ci = 0
    legend_x = ml + pw + 10
    legend_y = 40
    for name, pts in series.items():
        pts = dict(pts)
        pts2 = []
        for x in labels:
            if x in pts and pts[x] is not None:
                px = ml + pw * labels.index(x) / max(1, len(labels) - 1)
                py = mt + ph * (1 - (pts[x] - vmin) / (vmax - vmin))
                pts2.append((px, py))
        color = colors[ci % len(colors)]
        if pts2:
            parts.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>' % (
                color, " ".join("%.1f,%.1f" % p for p in pts2)))
        parts.append('<text x="%d" y="%d" font-size="10" fill="%s">%s</text>' % (legend_x, legend_y, color, _esc(name)))
        legend_y += 14
        ci += 1
    if ylabel:
        parts.append('<text x="14" y="%d" font-size="10" transform="rotate(-90 14 %d)">%s</text>' % (
            mt + ph / 2, mt + ph / 2, _esc(ylabel)))
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


def heatmap_svg(matrix, xlabels, ylabels, out_path, title, value_label="count",
                cell_w=46, cell_h=20):
    mt, ml, mb = 46, 78, 64
    width = ml + cell_w * max(1, len(xlabels)) + 20
    height = mt + cell_h * max(1, len(ylabels)) + mb
    vmax = max(max(row) for row in matrix) or 1
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height),
             '<rect width="100%%" height="100%%" fill="white"/>',
             '<text x="%d" y="20" font-size="14" font-family="sans-serif">%s</text>' % (ml, _esc(title))]
    for i, row in enumerate(matrix):
        parts.append('<text x="%d" y="%d" font-size="10" text-anchor="end">%s</text>' % (
            ml - 6, mt + i * cell_h + cell_h - 5, _esc(str(ylabels[i]))))
        for j, v in enumerate(row):
            x, y = ml + j * cell_w, mt + i * cell_h
            parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="#eee"/>' % (
                x, y, cell_w - 1, cell_h - 1, _palette(v, vmax)))
            if v:
                parts.append('<text x="%d" y="%d" font-size="9" text-anchor="middle">%d</text>' % (
                    x + (cell_w - 1) / 2, y + cell_h - 6, v))
    for j, x in enumerate(xlabels):
        parts.append('<text x="%d" y="%d" font-size="10" text-anchor="middle" transform="rotate(-45 %d %d)">%s</text>' % (
            ml + j * cell_w + (cell_w - 1) / 2, height - mb + 18,
            ml + j * cell_w + (cell_w - 1) / 2, height - mb + 18, _esc(str(x))))
    parts.append('<text x="%d" y="%d" font-size="10">%s</text>' % (ml, height - 8, _esc(value_label)))
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


def hbar_svg(items, out_path, title, xlabel="", width=920, height=480):
    ml, mr, mt, mb = 300, 60, 36, 30
    pw, ph = width - ml - mr, height - mt - mb
    vmax = max([v for _, v in items] + [1])
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height),
             '<rect width="100%%" height="100%%" fill="white"/>',
             '<text x="%d" y="20" font-size="14" font-family="sans-serif">%s</text>' % (ml, _esc(title))]
    items = items[:20]
    n = max(1, len(items))
    bh = min(24, ph / n * 0.8)
    for i, (label, v) in enumerate(items):
        y = mt + ph * i / n
        w = pw * v / vmax
        parts.append('<text x="%d" y="%d" font-size="10" text-anchor="end">%s</text>' % (ml - 6, y + bh * 0.65, _esc(str(label)[:42])))
        parts.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#4c72b0"/>' % (ml, y, max(1.0, w), bh))
        parts.append('<text x="%d" y="%d" font-size="10" fill="#333">%d</text>' % (ml + max(1.0, w) + 5, y + bh * 0.65, v))
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


def network_svg(graph, out_path, title, top_n=25, width=980, height=700):
    import networkx as nx
    nodes = sorted(graph.nodes(), key=lambda n: -graph.degree(n, weight="weight"))[:top_n]
    sub = graph.subgraph(nodes)
    pos = nx.spring_layout(sub, seed=42)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    pad = 60
    degs = [sub.degree(n, weight="weight") for n in nodes]
    dmin, dmax = min(degs), max(degs) if degs else 1
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height),
             '<rect width="100%%" height="100%%" fill="white"/>',
             '<text x="20" y="24" font-size="14" font-family="sans-serif">%s</text>' % _esc(title)]
    for a, b in sub.edges():
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        px1 = pad + (x1 - xmin) / max(1e-9, xmax - xmin) * (width - 2 * pad)
        py1 = height - pad - (y1 - ymin) / max(1e-9, ymax - ymin) * (height - 2 * pad - 40)
        px2 = pad + (x2 - xmin) / max(1e-9, xmax - xmin) * (width - 2 * pad)
        py2 = height - pad - (y2 - ymin) / max(1e-9, ymax - ymin) * (height - 2 * pad - 40)
        w = sub[a][b].get("weight", 1)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#9bb5d8" stroke-width="%.2f"/>' % (
            px1, py1, px2, py2, 0.4 + 2.0 * w / max(1, max(e[2].get("weight", 1) for e in sub.edges(data=True)))))
    for n in nodes:
        x, y = pos[n]
        px = pad + (x - xmin) / max(1e-9, xmax - xmin) * (width - 2 * pad)
        py = height - pad - (y - ymin) / max(1e-9, ymax - ymin) * (height - 2 * pad - 40)
        r = 4 + 14 * (sub.degree(n, weight="weight") - dmin) / max(1, dmax - dmin)
        parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#4c72b0" fill-opacity="0.75"/>' % (px, py, r))
        parts.append('<text x="%.1f" y="%.1f" font-size="8" text-anchor="middle">%s</text>' % (px, py + r + 9, _esc(str(n)[:30])))
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path

SEP = "=" * 60
SEP2 = "-" * 60






THEME_LEXICON = {
    "cybersecurity": ["cybersecurity", "cyber security", "cybercrime", "cyber crime", "cyber threats"],
    "ai-governance": ["artificial intelligence", "ai governance", "machine learning", "algorithmic decision"],
    "digital-inclusion": ["digital inclusion", "digital divide", "accessibility", "universal access"],
    "data-protection": ["data protection", "privacy", "personal data", "gdpr"],
    "human-rights": ["human rights", "freedom of expression", "censorship"],
    "infrastructure": ["infrastructure", "broadband", "connectivity", "5g"],
    "capacity-development": ["capacity building", "capacity development", "digital literacy"],
    "content-governance": ["content moderation", "platform regulation", "disinformation", "misinformation"],
    "gender": ["gender", "women", "girls"],
    "environment": ["climate", "environment", "sustainability", "e-waste"],
}

AI_TERMS = ["artificial intelligence", "ai governance", "ai regulation", "machine learning",
            "algorithmic", "large language model", "chatgpt", "generative ai", "autonomous"]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "")).strip().lower()


def load_records(args):
    if args.extraction:
        recs = []
        with open(args.extraction, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        pass
        return [r for r in recs if r.get("status") == "ok" and r.get("result")]
    data = json.load(open(args.json, encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("docs") or data.get("pages") or []
    out = []
    for r in data:
        result = {}
        result["title"] = r.get("title", "")
        result["year"] = r.get("year")
        result["session_type"] = r.get("type", "")
        result["themes"] = r.get("themes", [])
        result["sdgs"] = []
        result["keywords"] = r.get("keywords", [])
        result["summary"] = r.get("body_text", "")[:500]
        result["organizers"] = []
        result["speakers"] = r.get("speakers", [])
        out.append({"rel_path": r.get("rel_path", ""), "file": r.get("file", ""),
                    "type": r.get("type", ""), "year": r.get("year"),
                    "result": result})
    return out


def _page_text(rec):
    r = rec.get("result") or {}
    parts = [r.get("title") or "", r.get("summary") or ""]
    parts += [str(x) for x in (r.get("themes") or [])]
    parts += [str(k.get("kw", "")) for k in (r.get("keywords") or [])]
    return _norm(" ".join(parts))


def theme_counts(recs):
    per = defaultdict(Counter)
    docs_per_year = Counter()
    for r in recs:
        y = r.get("year")
        if not isinstance(y, int) or not (2006 <= y <= 2025):
            continue
        txt = _page_text(r)
        docs_per_year[y] += 1
        for theme, terms in THEME_LEXICON.items():
            if any(t in txt for t in terms):
                per[y][theme] += 1
    rows = []
    for y in sorted(per):
        for theme in sorted(THEME_LEXICON):
            rows.append((theme, y, per[y].get(theme, 0),
                         per[y].get(theme, 0) / max(1, docs_per_year[y])))
    return rows, docs_per_year


def sdg_counts(recs):
    per = defaultdict(Counter)
    sdg_re = re.compile(r"(?:sdg|goal)\s*#?\s*(\d{1,2})", re.I)
    for r in recs:
        y = r.get("year")
        if not isinstance(y, int) or not (2006 <= y <= 2025):
            continue
        res = r.get("result") or {}
        seen = set()
        for s in (res.get("sdgs") or []):
            m = sdg_re.search(str(s))
            if m and 1 <= int(m.group(1)) <= 17 and m.group(1) not in seen:
                seen.add(m.group(1))
                per[y][int(m.group(1))] += 1
        for kw in (res.get("keywords") or []):
            m = sdg_re.search(str(kw.get("kw", "")))
            if m and 1 <= int(m.group(1)) <= 17 and m.group(1) not in seen:
                seen.add(m.group(1))
                per[y][int(m.group(1))] += 1
    rows = []
    for y in sorted(per):
        for s in range(1, 18):
            rows.append((y, s, per[y].get(s, 0)))
    return rows


def _org_of(res):
    orgs = []
    for o in (res.get("organizers") or []):
        if str(o).strip():
            orgs.append(str(o).strip())
    for sp in (res.get("speakers") or []):
        if isinstance(sp, dict) and str(sp.get("organization") or "").strip():
            orgs.append(str(sp["organization"]).strip())
    return list(dict.fromkeys(orgs))


def org_network(recs):
    import networkx as nx

    g = nx.Graph()
    for r in recs:
        res = r.get("result") or {}
        orgs = _org_of(res)
        for i, a in enumerate(orgs):
            for b in orgs[i + 1:]:
                if g.has_edge(a, b):
                    g[a][b]["weight"] += 1
                else:
                    g.add_edge(a, b, weight=1)
    return g


def ai_case(recs):
    per = Counter()
    themes = Counter()
    orgs = Counter()
    kws = Counter()
    for r in recs:
        y = r.get("year")
        if not isinstance(y, int) or not (2017 <= y <= 2025):
            continue
        txt = _page_text(r)
        if not any(t in txt for t in AI_TERMS):
            continue
        per[y] += 1
        res = r.get("result") or {}
        for th in (res.get("themes") or []):
            themes[str(th).strip()] += 1
        for o in _org_of(res):
            orgs[o] += 1
        for k in (res.get("keywords") or []):
            kw = str(k.get("kw", "")).strip()
            if kw:
                kws[kw] += 1
    rows = [(y, per[y]) for y in sorted(per)]
    return rows, themes.most_common(10), orgs.most_common(10), kws.most_common(15)


def run(args):
    import networkx as nx

    recs = load_records(args)
    print("[ANALYSIS] loaded %d records" % len(recs))
    os.makedirs(args.out, exist_ok=True)

    rows, docs_per_year = theme_counts(recs)
    with open(os.path.join(args.out, "theme_trends.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["theme", "year", "count", "share"])
        w.writerows(rows)
    if rows:
        years = sorted({r[1] for r in rows})
        totals = Counter()
        for theme, y, c, s in rows:
            totals[theme] += c
        top = [t for t, _ in totals.most_common(6)]
        series = {}
        for theme in top:
            m = {y: 0.0 for y in years}
            for t, y, c, s in rows:
                if t == theme:
                    m[y] = s
            series[theme] = [(y, m[y]) for y in years]
        line_svg(series, os.path.join(args.out, "theme_trends.svg"),
                        "IGF theme trends", ylabel="share of pages per year")

    sdg_rows = sdg_counts(recs)
    with open(os.path.join(args.out, "sdg_matrix.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "sdg", "count"])
        w.writerows(sdg_rows)
    if sdg_rows:
        years = sorted({r[0] for r in sdg_rows})
        mat = []
        for s in range(1, 18):
            mat.append([next((c for yy, ss, c in sdg_rows if yy == y and ss == s), 0) for y in years])
        heatmap_svg(mat, [str(y) for y in years], ["SDG %d" % s for s in range(1, 18)],
                           os.path.join(args.out, "sdg_matrix.svg"), "SDG coverage by year")

    g = org_network(recs)
    if g.number_of_nodes():
        nx.write_graphml(g, os.path.join(args.out, "org_cooccurrence.graphml"))
        deg = sorted(g.degree(weight="weight"), key=lambda kv: -kv[1])
        with open(os.path.join(args.out, "org_degree.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["organization", "weighted_degree"])
            w.writerows(deg)
        hbar_svg(deg[:20], os.path.join(args.out, "org_degree.svg"),
                        "Top 20 organisations by co-occurrence degree")
        network_svg(g, os.path.join(args.out, "org_network.svg"),
                            "Organisation co-occurrence network (top 25 nodes)", top_n=25)
        print("  org network: %d nodes, %d edges" % (g.number_of_nodes(), g.number_of_edges()))

    ai_rows, ai_themes, ai_orgs, ai_kws = ai_case(recs)
    with open(os.path.join(args.out, "ai_governance_case.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "pages"])
        w.writerows(ai_rows)
        w.writerow([])
        w.writerow(["top_themes"])
        w.writerows(ai_themes)
        w.writerow([])
        w.writerow(["top_organizations"])
        w.writerows(ai_orgs)
        w.writerow([])
        w.writerow(["top_keywords"])
        w.writerows(ai_kws)
    if ai_rows:
        line_svg({"pages": [(str(y), c) for y, c in ai_rows]},
                        os.path.join(args.out, "ai_governance_case.svg"),
                        "AI-governance pages 2017-2025")
        print("  AI case: %d pages, top themes: %s" % (sum(c for _, c in ai_rows),
                                                       ", ".join(t for t, _ in ai_themes[:5])))

    summary = {
        "records": len(recs),
        "years": sorted(docs_per_year),
        "pages_per_year": dict(docs_per_year),
        "theme_totals": dict(Counter(t for t, y, c, s in rows for _ in range(c))),
        "sdg_totals": dict(Counter(s for y, s, c in sdg_rows for _ in range(c))),
        "org_network": {"nodes": g.number_of_nodes(), "edges": g.number_of_edges()},
        "ai_case": {"years": dict(ai_rows), "top_themes": ai_themes,
                    "top_orgs": ai_orgs, "top_keywords": ai_kws},
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("[ANALYSIS] outputs -> %s" % args.out)
    return 0


def downstream_main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--extraction", default="", help="extraction.jsonl from full_extract")
    ap.add_argument("--json", default="", help="alternative: all.json from the DOM extractor")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if not args.extraction and not args.json:
        print("need --extraction or --json")
        return 2
    return run(args)


# ---------------------------------------------------------------------------
# Topic attribution analysis (hot topics + topic x organisation/country)
# ---------------------------------------------------------------------------

COUNTRY_LEXICON = {
    "Afghanistan": ["afghanistan", "afghan"], "Albania": ["albania", "albanian"],
    "Algeria": ["algeria", "algerian"], "Argentina": ["argentina", "argentine", "argentinian"],
    "Armenia": ["armenia", "armenian"], "Australia": ["australia", "australian"],
    "Austria": ["austria", "austrian"], "Azerbaijan": ["azerbaijan", "azerbaijani"],
    "Bahamas": ["bahamas", "bahamian"], "Bahrain": ["bahrain", "bahraini"],
    "Bangladesh": ["bangladesh", "bangladeshi"], "Barbados": ["barbados", "barbadian"],
    "Belarus": ["belarus", "belarusian"], "Belgium": ["belgium", "belgian"],
    "Belize": ["belize"], "Benin": ["benin"], "Bhutan": ["bhutan", "bhutanese"],
    "Bolivia": ["bolivia", "bolivian"], "Bosnia and Herzegovina": ["bosnia", "herzegovina"],
    "Botswana": ["botswana"], "Brazil": ["brazil", "brazilian"],
    "Brunei": ["brunei", "bruneian"], "Bulgaria": ["bulgaria", "bulgarian"],
    "Burkina Faso": ["burkina faso", "burkinabe"], "Burundi": ["burundi", "burundian"],
    "Cambodia": ["cambodia", "cambodian"], "Cameroon": ["cameroon", "cameroonian"],
    "Canada": ["canada", "canadian"], "Chad": ["chad"], "Chile": ["chile", "chilean"],
    "China": ["china", "chinese"], "Colombia": ["colombia", "colombian"],
    "Congo": ["congo", "congolese"], "Costa Rica": ["costa rica", "costa rican"],
    "Croatia": ["croatia", "croatian"], "Cuba": ["cuba", "cuban"], "Cyprus": ["cyprus", "cypriot"],
    "Czechia": ["czechia", "czech republic", "czech"], "Denmark": ["denmark", "danish"],
    "Dominican Republic": ["dominican republic", "dominican"], "Ecuador": ["ecuador", "ecuadorian"],
    "Egypt": ["egypt", "egyptian"], "El Salvador": ["el salvador", "salvadoran"],
    "Estonia": ["estonia", "estonian"], "Eswatini": ["eswatini", "swaziland"],
    "Ethiopia": ["ethiopia", "ethiopian"], "Fiji": ["fiji", "fijian"],
    "Finland": ["finland", "finnish"], "France": ["france", "french"],
    "Gabon": ["gabon", "gabonese"], "Gambia": ["gambia", "gambian"],
    "Georgia": ["georgia", "georgian"], "Germany": ["germany", "german"],
    "Ghana": ["ghana", "ghanaian"], "Greece": ["greece", "greek"],
    "Guatemala": ["guatemala", "guatemalan"], "Guinea": ["guinea", "guinean"],
    "Guyana": ["guyana", "guyanese"], "Haiti": ["haiti", "haitian"],
    "Honduras": ["honduras", "honduran"], "Hungary": ["hungary", "hungarian"],
    "Iceland": ["iceland", "icelandic"], "India": ["india", "indian"],
    "Indonesia": ["indonesia", "indonesian"], "Iran": ["iran", "iranian"],
    "Iraq": ["iraq", "iraqi"], "Ireland": ["ireland", "irish"],
    "Israel": ["israel", "israeli"], "Italy": ["italy", "italian"],
    "Jamaica": ["jamaica", "jamaican"], "Japan": ["japan", "japanese"],
    "Jordan": ["jordan", "jordanian"], "Kazakhstan": ["kazakhstan", "kazakh"],
    "Kenya": ["kenya", "kenyan"], "Kuwait": ["kuwait", "kuwaiti"],
    "Kyrgyzstan": ["kyrgyzstan", "kyrgyz"], "Laos": ["laos", "lao"],
    "Latvia": ["latvia", "latvian"], "Lebanon": ["lebanon", "lebanese"],
    "Liberia": ["liberia", "liberian"], "Libya": ["libya", "libyan"],
    "Lithuania": ["lithuania", "lithuanian"], "Luxembourg": ["luxembourg"],
    "Madagascar": ["madagascar", "malagasy"], "Malawi": ["malawi", "malawian"],
    "Malaysia": ["malaysia", "malaysian"], "Maldives": ["maldives", "maldivian"],
    "Mali": ["mali", "malian"], "Malta": ["malta", "maltese"],
    "Mauritania": ["mauritania", "mauritanian"], "Mauritius": ["mauritius", "mauritian"],
    "Mexico": ["mexico", "mexican"], "Moldova": ["moldova", "moldovan"],
    "Mongolia": ["mongolia", "mongolian"], "Montenegro": ["montenegro", "montenegrin"],
    "Morocco": ["morocco", "moroccan"], "Mozambique": ["mozambique", "mozambican"],
    "Myanmar": ["myanmar", "burma", "burmese"], "Namibia": ["namibia", "namibian"],
    "Nepal": ["nepal", "nepali"], "Netherlands": ["netherlands", "dutch"],
    "New Zealand": ["new zealand"], "Nicaragua": ["nicaragua", "nicaraguan"],
    "Niger": ["niger", "nigerien"], "Nigeria": ["nigeria", "nigerian"],
    "North Korea": ["north korea", "dprk"], "North Macedonia": ["north macedonia", "macedonia", "macedonian"],
    "Norway": ["norway", "norwegian"], "Oman": ["oman", "omani"],
    "Pakistan": ["pakistan", "pakistani"], "Palestine": ["palestine", "palestinian"],
    "Panama": ["panama", "panamanian"], "Paraguay": ["paraguay", "paraguayan"],
    "Peru": ["peru", "peruvian"], "Philippines": ["philippines", "filipino", "philippine"],
    "Poland": ["poland", "polish"], "Portugal": ["portugal", "portuguese"],
    "Qatar": ["qatar", "qatari"], "Romania": ["romania", "romanian"],
    "Russia": ["russia", "russian"], "Rwanda": ["rwanda", "rwandan"],
    "Saudi Arabia": ["saudi arabia", "saudi"], "Senegal": ["senegal", "senegalese"],
    "Serbia": ["serbia", "serbian"], "Sierra Leone": ["sierra leone", "sierra leonean"],
    "Singapore": ["singapore", "singaporean"], "Slovakia": ["slovakia", "slovak"],
    "Slovenia": ["slovenia", "slovenian"], "Somalia": ["somalia", "somali"],
    "South Africa": ["south africa", "south african"], "South Korea": ["south korea", "republic of korea", "korea", "korean"],
    "Spain": ["spain", "spanish"], "Sri Lanka": ["sri lanka", "sri lankan"],
    "Sudan": ["sudan", "sudanese"], "Suriname": ["suriname", "surinamese"],
    "Sweden": ["sweden", "swedish"], "Switzerland": ["switzerland", "swiss"],
    "Syria": ["syria", "syrian"], "Taiwan": ["taiwan", "taiwanese"],
    "Tajikistan": ["tajikistan", "tajik"], "Tanzania": ["tanzania", "tanzanian"],
    "Thailand": ["thailand", "thai"], "Timor-Leste": ["timor leste", "east timor", "timorese"],
    "Togo": ["togo", "togolese"], "Trinidad and Tobago": ["trinidad", "tobago"],
    "Tunisia": ["tunisia", "tunisian"], "Turkey": ["turkey", "turkish", "türkiye"],
    "Turkmenistan": ["turkmenistan", "turkmen"], "Uganda": ["uganda", "ugandan"],
    "Ukraine": ["ukraine", "ukrainian"], "United Arab Emirates": ["united arab emirates", "uae", "emirati"],
    "United Kingdom": ["united kingdom", "uk", "britain", "british", "england", "scotland", "wales"],
    "United States": ["united states", "usa", "u.s.", "america", "american"],
    "Uruguay": ["uruguay", "uruguayan"], "Uzbekistan": ["uzbekistan", "uzbek"],
    "Venezuela": ["venezuela", "venezuelan"], "Vietnam": ["vietnam", "vietnamese"],
    "Yemen": ["yemen", "yemeni"], "Zambia": ["zambia", "zambian"], "Zimbabwe": ["zimbabwe", "zimbabwean"],
}

_COUNTRY_RE = []
for _cname, _aliases in sorted(COUNTRY_LEXICON.items(), key=lambda kv: -max(len(a) for a in kv[1])):
    _pattern = r"\b(?:" + "|".join(re.escape(a) for a in _aliases) + r")\b"
    _COUNTRY_RE.append((_cname, re.compile(_pattern, re.I)))
del _cname, _aliases, _pattern

TOPIC_ORG_FIELD_RE = re.compile(
    r"speaker|organi[sz]er|co_organi[sz]er|co-organi[sz]er|proposer|panelist|moderator", re.I)


def _topic_entity_text(res):
    parts = []
    for key, val in (res or {}).items():
        if not val:
            continue
        if not TOPIC_ORG_FIELD_RE.search(key):
            continue
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    for k2 in ("organization", "org", "affiliation", "name", "title"):
                        if str(item.get(k2) or "").strip():
                            parts.append(str(item[k2]).strip())
                elif str(item).strip():
                    parts.append(str(item).strip())
        elif isinstance(val, dict):
            for k2 in ("organization", "org", "affiliation", "name"):
                if str(val.get(k2) or "").strip():
                    parts.append(str(val[k2]).strip())
        elif str(val).strip():
            parts.append(str(val).strip())
    return list(dict.fromkeys(p for p in parts if p and len(p) < 120))


def _page_topics(rec):
    txt = _page_text(rec)
    if not txt:
        txt = _norm(" ".join(str(t) for t in ((rec.get("result") or {}).get("themes") or [])))
    hits = [theme for theme, terms in THEME_LEXICON.items()
            if any(t in txt for t in terms)]
    return hits


def _orgs_of_page(rec):
    res = rec.get("result") or {}
    orgs = _org_of(res)
    orgs += _topic_entity_text(res)
    out = []
    for o in orgs:
        o = str(o).replace("\xa0", " ").strip().rstrip(",;.")
        if not o:
            continue
        if len(o) > 120 or "@" in o or re.search(r"\bemail\s+protected\b", o, re.I):
            continue
        if ENTITY_JUNK_RE.search(o):
            continue
        if re.match(r"^(mr|ms|mrs|dr|prof|professor|keynote|moderator|panelists?)\b", o, re.I):
            continue
        if re.match(r"^speaker\s*\d+", o, re.I):
            continue
        if re.match(r"^[A-Z][a-z]+(\s+[A-Z][a-z]+)*$", o) and len(o) < 4:
            continue
        if o not in out:
            out.append(o)
    return out


def build_org_country_map(orgs):
    mapping = {}
    for org in orgs:
        found = None
        for country, cre in _COUNTRY_RE:
            if cre.search(org):
                found = country
                break
        mapping[org] = found
    mapped = sum(1 for v in mapping.values() if v)
    coverage = (mapped / len(mapping)) if mapping else 0.0
    return mapping, {"orgs": len(mapping), "mapped": mapped,
                     "unknown": len(mapping) - mapped, "coverage": round(coverage, 4)}


def _topic_org_pairs(recs):
    pairs = Counter()
    org_topic_pages = Counter()
    for rec in recs:
        topics = _page_topics(rec)
        if not topics:
            continue
        orgs = _orgs_of_page(rec)
        for topic in topics:
            for org in orgs:
                pairs[(topic, org)] += 1
                org_topic_pages[(org, topic)] += 1
    return pairs, org_topic_pages


def topic_org_matrix(recs):
    pairs, _ = _topic_org_pairs(recs)
    rows = [(t, o, c) for (t, o), c in pairs.items()]
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return rows


def topic_country_matrix(recs, org_country_map):
    pairs, _ = _topic_org_pairs(recs)
    agg = Counter()
    for (topic, org), count in pairs.items():
        country = org_country_map.get(org)
        if country:
            agg[(topic, country)] += count
    rows = [(t, c, v) for (t, c), v in agg.items()]
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return rows


def top_topics(recs):
    counts = Counter()
    for rec in recs:
        for topic in _page_topics(rec):
            counts[topic] += 1
    return counts


def topic_leaders(recs, org_country_map):
    pairs, _ = _topic_org_pairs(recs)
    by_topic = defaultdict(Counter)
    by_topic_country = defaultdict(Counter)
    for (topic, org), count in pairs.items():
        by_topic[topic][org] += count
        country = org_country_map.get(org)
        if country:
            by_topic_country[topic][country] += count
    rows = []
    for topic in sorted(by_topic):
        for org, count in by_topic[topic].most_common(10):
            rows.append((topic, "org", org, count))
        for country, count in by_topic_country[topic].most_common(10):
            rows.append((topic, "country", country, count))
    return rows


def _write_topic_csv(out_dir, name, header, rows):
    with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return os.path.join(out_dir, name)


def bipartite_svg(left, right, edges, out_path, title, width=980, height=700):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height),
             '<rect width="100%%" height="100%%" fill="white"/>',
             '<text x="20" y="24" font-size="14" font-family="sans-serif">%s</text>' % _esc(title)]
    pad = 70
    lx = width * 0.2
    rx = width * 0.8
    lpos = {}
    rpos = {}
    lstep = (height - 2 * pad) / max(1, len(left) - 1)
    rstep = (height - 2 * pad) / max(1, len(right) - 1)
    for i, node in enumerate(left):
        y = pad + i * lstep
        lpos[node] = (lx, y)
        parts.append('<circle cx="%.1f" cy="%.1f" r="6" fill="#4c72b0"/>' % (lx, y))
        parts.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="end">%s</text>' % (lx - 10, y + 3, _esc(str(node)[:38])))
    for i, node in enumerate(right):
        y = pad + i * rstep
        rpos[node] = (rx, y)
        parts.append('<circle cx="%.1f" cy="%.1f" r="6" fill="#dd8452"/>' % (rx, y))
        parts.append('<text x="%.1f" y="%.1f" font-size="9">%s</text>' % (rx + 10, y + 3, _esc(str(node)[:38])))
    if edges:
        wmax = max(v for _, _, v in edges)
        for a, b, v in edges:
            if a not in lpos or b not in rpos:
                continue
            x1, y1 = lpos[a]
            x2, y2 = rpos[b]
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#9bb5d8" stroke-width="%.2f"/>' % (
                x1, y1, x2, y2, 0.5 + 2.5 * v / max(1, wmax)))
    parts.append('</svg>')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


def topic_analysis(recs, out_dir):
    counts = top_topics(recs)
    if not counts:
        return {"error": "no topic hits"}
    with open(os.path.join(out_dir, "topic_topics.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "pages"])
        w.writerows(sorted(counts.items(), key=lambda kv: -kv[1]))
    if counts:
        hbar_svg(list(counts.most_common(12)), os.path.join(out_dir, "topic_hot_topics.svg"),
                 "Hot topics by page hits", xlabel="pages")
        with open(os.path.join(out_dir, "topic_hot_topics.svg"), encoding="utf-8") as _f:
            with open(os.path.join(out_dir, "hot_topics.svg"), "w", encoding="utf-8") as _g:
                _g.write(_f.read())

    rows, docs_per_year = theme_counts(recs)
    with open(os.path.join(out_dir, "topic_year_matrix.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "year", "pages"])
        w.writerows([(t, y, c) for t, y, c, s in rows])
    if rows:
        years = sorted({r[1] for r in rows})
        mat = [[next((c for t, y, c, s in rows if t == topic and y == yy), 0) for yy in years]
               for topic in sorted(THEME_LEXICON)]
        heatmap_svg(mat, [str(y) for y in years], sorted(THEME_LEXICON),
                    os.path.join(out_dir, "topic_year_heatmap.svg"), "Topic hits by year")

    org_rows = topic_org_matrix(recs)
    with open(os.path.join(out_dir, "topic_org_matrix.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "organization", "pages"])
        w.writerows(org_rows)
    all_orgs = sorted({r[1] for r in org_rows})
    org_country_map, map_stats = build_org_country_map(all_orgs)
    with open(os.path.join(out_dir, "topic_org_country_map.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["organization", "country"])
        w.writerows([(o, org_country_map.get(o) or "unknown") for o in all_orgs])

    country_rows = topic_country_matrix(recs, org_country_map)
    with open(os.path.join(out_dir, "topic_country_matrix.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "country", "pages"])
        w.writerows(country_rows)

    leaders = topic_leaders(recs, org_country_map)
    _write_topic_csv(out_dir, "topic_leaders.csv",
                     ["topic", "kind", "name", "pages"], leaders)

    if org_rows:
        top_orgs = sorted(Counter({r[1]: r[2] for r in org_rows}).items(), key=lambda kv: -kv[1])[:20]
        topics = sorted({r[0] for r in org_rows})
        mat = []
        for topic in topics:
            row = []
            cnt = {r[1]: r[2] for r in org_rows if r[0] == topic}
            for org, _ in top_orgs:
                row.append(cnt.get(org, 0))
            mat.append(row)
        heatmap_svg(mat, [o[:14] for o, _ in top_orgs], topics,
                    os.path.join(out_dir, "topic_org_heatmap.svg"),
                    "Topic x organisation page hits (top 20 orgs)")
        left = sorted(THEME_LEXICON)[:10]
        right = [o for o, _ in top_orgs[:10]]
        edges = [(t, o, next((c for tt, oo, c in org_rows if tt == t and oo == o), 0))
                 for t in left for o in right]
        edges = [e for e in edges if e[2] > 0]
        if edges:
            bipartite_svg(left, right, edges, os.path.join(out_dir, "topic_org_bipartite.svg"),
                          "Topic-organisation bipartite (page co-occurrence)")

    if country_rows:
        top_countries = sorted(Counter({r[1]: r[2] for r in country_rows}).items(), key=lambda kv: -kv[1])[:20]
        topics = sorted({r[0] for r in country_rows})
        mat = []
        for topic in topics:
            cnt = {r[1]: r[2] for r in country_rows if r[0] == topic}
            mat.append([cnt.get(c, 0) for c, _ in top_countries])
        heatmap_svg(mat, [c for c, _ in top_countries], topics,
                    os.path.join(out_dir, "topic_country_heatmap.svg"),
                    "Topic x country page hits (top 20 countries)")
    else:
        with open(os.path.join(out_dir, "topic_country_heatmap.txt"), "w", encoding="utf-8") as f:
            f.write("No organisation mapped to a country; topic_country_heatmap.svg not generated.\n")

    summary = {
        "records": len(recs),
        "top_topics": counts.most_common(10),
        "org_country_map": map_stats,
        "org_rows": len(org_rows),
        "country_rows": len(country_rows),
        "leaders_rows": len(leaders),
    }
    with open(os.path.join(out_dir, "topic_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


def _load_topic_records(args):
    label_entity = re.compile(r"speaker|organi[sz]er|proposer|moderator|panelist|rapporteur", re.I)
    label_nonentity = re.compile(r"format|duration|room|time|language|session type|subtheme|theme|sdg", re.I)
    if getattr(args, "extraction", ""):
        recs = []
        with open(args.extraction, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("status") == "ok" and r.get("result"):
                    recs.append(r)
        return recs
    data = json.load(open(args.json, encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("docs") or data.get("pages") or []
    out = []
    for r in data:
        df = r.get("drupal_fields") or {}
        themes = []
        speakers = []
        for key, val in df.items():
            if not isinstance(val, dict):
                continue
            label = str(val.get("label") or "")
            contents = []
            for item in val.get("content") or []:
                if isinstance(item, dict):
                    contents.append(str(item.get("text") or "").strip())
            text = " ".join(c for c in contents if c)
            if not text:
                continue
            if key.startswith("theme"):
                themes.append(text)
            elif label_nonentity.search(label):
                continue
            elif label_entity.search(label) or re.match(r"speaker|organi[sz]er|co_organi[sz]er|proposer|panelist", key, re.I):
                speakers.append(text)
        result = {
            "title": r.get("title") or "",
            "year": r.get("year"),
            "session_type": r.get("type") or "",
            "themes": themes,
            "keywords": [],
            "summary": (r.get("body_text") or "")[:800],
            "organizers": [],
            "speakers": [{"organization": t} for t in speakers if t],
        }
        out.append({"rel_path": r.get("rel_path", ""), "file": r.get("file", ""),
                    "type": r.get("type", ""), "year": r.get("year"),
                    "result": result})
    return out


def hot_topics_main(argv=None):
    ap = argparse.ArgumentParser(prog="igf hot-topics")
    ap.add_argument("--extraction", default="", help="extraction.jsonl from full_extract")
    ap.add_argument("--json", default="", help="alternative: all.json from the DOM extractor")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if not args.extraction and not args.json:
        print("need --extraction or --json")
        return 2
    recs = _load_topic_records(args)
    print("[TOPICS] loaded %d records" % len(recs))
    os.makedirs(args.out, exist_ok=True)
    summary = topic_analysis(recs, args.out)
    if "error" in summary:
        print("[TOPICS] %s" % summary["error"])
        return 1
    print("[TOPICS] top topics: %s" % ", ".join("%s=%d" % kv for kv in summary["top_topics"][:8]))
    print("[TOPICS] org->country: mapped=%d unknown=%d coverage=%.1f%%" % (
        summary["org_country_map"]["mapped"], summary["org_country_map"]["unknown"],
        summary["org_country_map"]["coverage"] * 100))
    print("[TOPICS] outputs -> %s" % args.out)
    return 0


# ---------------------------------------------------------------------------
# Cross-validation of crawled pages (self-consistency + official listing +
# optional Wayback CDX / Indico corroboration)
# ---------------------------------------------------------------------------

LIST_FILE_RE = re.compile(
    r"^(igf-\d{4}-(workshops|open-forums|lightning-talks|day-0-events|launches-awards|"
    r"networking-sessions|main-sessions|town-halls|transcripts|schedule|report)(-\d+)?|"
    r"(workshop|open-forum|lightning-talk|pre-events|launches-awards|networking-sessions)-proposals-\d{4}"
    r")\.html$", re.I)

_CANONICAL_RE = re.compile(
    r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", re.I)
_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


def _norm_verify_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    url = crawl._unwrap_wb(url) or url
    url = urllib.parse.urljoin("https://intgovforum.org/", url)
    url = url.split("#")[0].split("?")[0]
    return url.rstrip("/")


def _cdx_check(url, min_interval=0.25):
    import time as _t
    import urllib.request as _u
    if "_LAST_CDX" not in globals():
        globals()["_LAST_CDX"] = 0.0
    wait = min_interval - (_t.monotonic() - globals()["_LAST_CDX"])
    if wait > 0:
        _t.sleep(wait)
    globals()["_LAST_CDX"] = _t.monotonic()
    try:
        q = ("https://web.archive.org/cdx/search/cdx?url=%s&output=json&limit=1"
             "&filter=statuscode:200" % urllib.parse.quote(url, safe=""))
        req = _u.Request(q, headers={"User-Agent": "igf-cross-validate/1.0"})
        with _u.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        return "yes" if len(data) > 1 else "no"
    except Exception:
        return "cdx_unavailable"


def _indico_check(url):
    import urllib.request as _u
    m = re.search(r"event/(\d+)", url or "")
    if not m:
        return "no_event_id"
    try:
        q = "https://indico.un.org/export/event/%s.json" % m.group(1)
        req = _u.Request(q, headers={"User-Agent": "igf-cross-validate/1.0"})
        with _u.urlopen(req, timeout=10) as r:
            return "yes" if r.status == 200 else "no"
    except Exception:
        return "indico_unavailable"


def _listing_sources(full_dir):
    official = set()
    sources = []
    for root, _dirs, files in os.walk(full_dir):
        for name in files:
            if not name.lower().endswith(".html"):
                continue
            if not LIST_FILE_RE.match(name):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    html = f.read()
            except Exception:
                continue
            hrefs = [_norm_verify_url(h) for h in _HREF_RE.findall(html)]
            hrefs = [h for h in hrefs if h]
            sources.append((path, hrefs))
            official.update(hrefs)
    return sources, official


def _self_check(html, fname, canonical):
    if len(html) < 300:
        return "too_short", ""
    try:
        import warnings
        from bs4 import XMLParsedAsHTMLWarning
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(html, "html.parser")
        crawl._strip_noise(soup)
        main = soup.find("main") or soup.find(id="main-content") or soup.find("body") or soup
        text = main.get_text(separator=" ", strip=True)
    except Exception:
        return "parse_error", ""
    if len(text) < 300:
        return "thin_body", text
    low = html[:3000].lower()
    if "cf-browser-verify" in low or "just a moment" in low:
        return "blocked_page", text
    if "access denied" in low and len(html) < 2000:
        return "blocked_page", text
    title = soup.title.get_text(strip=True).lower() if soup.title else ""
    if re.search(r"404|page not found|not found", title):
        return "not_found_page", text
    src = (canonical or fname or "")
    ym = re.search(r"(19|20)(\d{2})", src)
    if ym and 2006 <= int(ym.group(0)) <= 2026:
        year = ym.group(0)
        probe = (title + " " + text[:2000]).lower()
        if year not in probe:
            return "year_unconfirmed", text
    return "consistent", text


def cross_validate_main(argv=None):
    ap = argparse.ArgumentParser(prog="igf cross-validate")
    ap.add_argument("--full", required=True, help="igf_full_* crawl directory")
    ap.add_argument("--wayback", action="store_true", help="query Wayback CDX (network)")
    ap.add_argument("--indico", action="store_true", help="query Indico API for participants pages (network)")
    ap.add_argument("--limit", type=int, default=None, help="cap validated pages")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    html_files = []
    for root, _dirs, files in os.walk(args.full):
        for name in files:
            if name.lower().endswith(".html"):
                html_files.append(os.path.join(root, name))
    html_files.sort()

    print("[XVAL] scanning %d html files for listing sources" % len(html_files))
    sources, official = _listing_sources(args.full)

    canonical_map = {}
    for path in html_files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(40000)
        except Exception:
            continue
        m = _CANONICAL_RE.search(head)
        if m:
            canonical_map[path] = _norm_verify_url(m.group(1))

    listed_set = set(canonical_map.values())

    total = len(html_files)
    target = html_files if args.limit is None else html_files[:args.limit]
    os.makedirs(args.out, exist_ok=True)
    tsv_path = os.path.join(args.out, "cross_validation.tsv")
    rows = []
    indico_stats = Counter()
    with open(tsv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["url", "path", "self_consistency", "listed_on_official", "archived_exists", "verdict"])
        for i, path in enumerate(target, 1):
            rel = os.path.relpath(path, args.full).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    html = f.read()
            except Exception:
                html = ""
            canonical = canonical_map.get(path, "")
            self_status, _text = _self_check(html, os.path.basename(path), canonical)

            listed = "unknown"
            if canonical and listed_set:
                listed = "yes" if canonical in official else "no"

            archived = "not_requested"
            if args.wayback and canonical:
                archived = _cdx_check(canonical)

            indico = "not_requested"
            if args.indico and canonical and "07_participants" in rel.replace("\\", "/"):
                indico = _indico_check(canonical)
                indico_stats[indico] += 1

            if self_status in ("too_short", "blocked_page",
                               "not_found_page", "parse_error"):
                verdict = "failed"
            elif self_status == "thin_body":
                verdict = "uncertain"
            elif listed == "yes" and self_status == "consistent":
                verdict = "verified"
            elif archived == "yes" and self_status == "consistent":
                verdict = "archive_only"
            elif self_status == "year_unconfirmed":
                verdict = "uncertain"
            elif not canonical and not args.wayback:
                verdict = "uncertain"
            else:
                verdict = "self_consistent_only"

            w.writerow([canonical or "", rel, self_status, listed, archived, verdict])
            rows.append((rel, self_status, listed, archived, verdict, canonical or ""))
            if i % 500 == 0:
                print("[XVAL] %d/%d" % (i, len(target)))

    self_dist = Counter(r[1] for r in rows)
    listed_dist = Counter(r[2] for r in rows)
    archived_dist = Counter(r[3] for r in rows)
    verdict_dist = Counter(r[4] for r in rows)
    failed = [r for r in rows if r[4] == "failed"]
    listed_sources_note = ("%d list pages parsed, %d distinct urls collected, "
                           "%d/%d pages have canonical url" % (
                               len(sources), len(official),
                               sum(1 for p in html_files if p in canonical_map), total))

    report = []
    report.append("CROSS VALIDATION REPORT")
    report.append("full_dir: %s" % args.full)
    report.append("html_files: %d (validated: %d)" % (total, len(rows)))
    report.append("listing: %s" % listed_sources_note)
    report.append("")
    report.append("SELF CONSISTENCY")
    for k in sorted(self_dist):
        report.append("  %-20s %d (%.1f%%)" % (k, self_dist[k], 100.0 * self_dist[k] / max(1, len(rows))))
    report.append("LISTED ON OFFICIAL")
    for k in sorted(listed_dist):
        report.append("  %-20s %d (%.1f%%)" % (k, listed_dist[k], 100.0 * listed_dist[k] / max(1, len(rows))))
    report.append("ARCHIVED (wayback=%s)" % ("on" if args.wayback else "off"))
    for k in sorted(archived_dist):
        report.append("  %-20s %d" % (k, archived_dist[k]))
    if args.indico:
        report.append("INDICO (participants pages)")
        for k in sorted(indico_stats):
            report.append("  %-20s %d" % (k, indico_stats[k]))
    report.append("VERDICT")
    for k in ["verified", "self_consistent_only", "archive_only", "uncertain", "failed"]:
        report.append("  %-22s %d (%.1f%%)" % (k, verdict_dist.get(k, 0),
                                               100.0 * verdict_dist.get(k, 0) / max(1, len(rows))))
    ok = len(rows) - verdict_dist.get("failed", 0)
    report.append("self_ok_rate: %.1f%% (%d/%d)" % (100.0 * ok / max(1, len(rows)), ok, len(rows)))
    report.append("")
    report.append("VERDICT DEFINITIONS")
    report.append("  verified: self-check fully passed (consistent) and page URL found on an official list page")
    report.append("  self_consistent_only: self-check passed, no official listing or archive corroboration")
    report.append("  archive_only: self-check passed, Wayback CDX snapshot exists, not listed")
    report.append("  uncertain: thin but valid body, or year unconfirmed, or no canonical url / CDX unavailable")
    report.append("  failed: too-short file, blocked page, 404 page or parse error (invalid page)")
    report.append("")
    report.append("FAILED SAMPLES (up to 10)")
    for rel, st, ld, ar, vd, url in failed[:10]:
        report.append("  %-60s %s" % (rel[:60], st))
    report.append("")
    report.append("NOTES")
    if not args.wayback:
        report.append("  wayback CDX not run (pass --wayback; needs network access)")
    if not args.indico:
        report.append("  indico API not run (pass --indico; needs network access)")
    report.append("  verdicts are per-page evidence levels, not a claim that content is official text")
    with open(os.path.join(args.out, "cross_validation_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")

    hbar_svg([(k, verdict_dist.get(k, 0)) for k in
              ["verified", "self_consistent_only", "archive_only", "uncertain", "failed"]],
             os.path.join(args.out, "cross_validation.svg"),
             "Cross-validation verdict distribution", xlabel="pages")

    print("[XVAL] %d pages: verified=%d self_only=%d archive_only=%d uncertain=%d failed=%d" % (
        len(rows), verdict_dist.get("verified", 0), verdict_dist.get("self_consistent_only", 0),
        verdict_dist.get("archive_only", 0), verdict_dist.get("uncertain", 0),
        verdict_dist.get("failed", 0)))
    print("[XVAL] listed: yes=%d no=%d unknown=%d; archived: %s" % (
        listed_dist.get("yes", 0), listed_dist.get("no", 0), listed_dist.get("unknown", 0),
        "yes=%d no=%d unavailable=%d" % (archived_dist.get("yes", 0),
                                         archived_dist.get("no", 0),
                                         archived_dist.get("cdx_unavailable", 0))
        if args.wayback else "not requested"))
    print("[XVAL] outputs -> %s" % args.out)
    return 0
