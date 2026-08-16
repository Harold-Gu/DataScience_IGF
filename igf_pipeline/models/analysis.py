#!/usr/bin/env python3
"""Deep-dive analysis of the denoised IGF JSON corpus.

Produces, without pandas/matplotlib:
  - type x year matrix            (CSV + HTML heatmap + console)
  - yearly TF-IDF keywords        (sklearn, English stop words)
  - topic-drift keyword series    (regex counters per 100k body chars)
  - SDG distribution              (parsed from the "GOAL n" Drupal field)
  - Drupal field coverage by type (report vs proposal fields)
  - organization co-occurrence network (networkx, GEXF + CSV + console)
  - external link domain stats
  - body length stats per type
  - duplicate detection via content_hash
"""

import argparse
import csv
import html
import json
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import mean, median

import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer


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
    """Join the text of every Drupal field whose name matches key_re."""
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


def main(argv=None):
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


if __name__ == '__main__':
    sys.exit(main())
