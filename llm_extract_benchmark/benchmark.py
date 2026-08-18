#!/usr/bin/env python3
"""Models x methods over the gold transcript set, scored per field."""

import argparse
import csv
import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from extractors import METHODS, run_rules


HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = ['qwen3.5:9b', 'qwen3:8b', 'qwen3.5:4b', 'qwen3.6:latest', 'qwen2.5:latest']
DEFAULT_METHODS = ['rules', 'oneshot', 'fewshot', 'fieldqa', 'tools', 'cited', 'chunked']


def normalize_name(value):
    value = re.sub(r'[^A-Z0-9 ]', ' ', str(value).upper())
    tokens = [t for t in value.split() if t]
    return ' '.join(tokens)


def set_f1(predicted, gold):
    predicted = {normalize_name(x) for x in (predicted or []) if normalize_name(x)}
    gold = {normalize_name(x) for x in (gold or [])}
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold:
        return 0.0
    inter = len(predicted & gold)
    precision = inter / len(predicted)
    recall = inter / len(gold)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def theme_match(left, right):
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens))) >= 0.5


def theme_f1(predicted, gold):
    predicted = [x for x in (predicted or []) if isinstance(x, str) and x.strip()]
    gold = [x for x in (gold or []) if isinstance(x, str) and x.strip()]
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold:
        return 0.0
    matched_gold = 0
    used_gold = set()
    for theme in predicted:
        for index, gold_theme in enumerate(gold):
            if index not in used_gold and theme_match(theme, gold_theme):
                matched_gold += 1
                used_gold.add(index)
                break
    precision = matched_gold / len(predicted)
    recall = matched_gold / len(gold)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def rouge_l(predicted, gold):
    predicted = [t for t in re.split(r'\W+', (predicted or '').lower()) if t]
    gold = [t for t in re.split(r'\W+', (gold or '').lower()) if t]
    if not predicted or not gold:
        return 0.0
    lengths = [[0] * (len(gold) + 1) for _ in range(len(predicted) + 1)]
    for i in range(1, len(predicted) + 1):
        for j in range(1, len(gold) + 1):
            lengths[i][j] = (lengths[i - 1][j - 1] + 1 if predicted[i - 1] == gold[j - 1]
                             else max(lengths[i - 1][j], lengths[i][j - 1]))
    lcs = lengths[len(predicted)][len(gold)]
    return 2 * lcs / (len(predicted) + len(gold)) if lcs else 0.0


def quote_in_text(quote, text):
    quote_norm = re.sub(r'\s+', ' ', str(quote or '')).strip().lower()
    text_norm = re.sub(r'\s+', ' ', (text or '')).lower()
    if not quote_norm:
        return True
    if quote_norm in text_norm or text_norm in quote_norm:
        return True
    quote_tokens = [t for t in re.split(r'\W+', quote_norm) if t]
    if not quote_tokens:
        return False
    text_tokens = [t for t in re.split(r'\W+', text_norm) if t]
    window = len(quote_tokens)
    best = 0
    for i in range(0, len(text_tokens) - window + 1):
        match = sum(1 for a, b in zip(quote_tokens, text_tokens[i:i + window]) if a == b)
        best = max(best, match)
    return best / len(quote_tokens) >= 0.7


def grounding_rate(result, text):
    if not isinstance(result, dict) or result.get('error'):
        return None
    quotes = []
    for field in ('title', 'year', 'venue', 'session_type', 'speakers', 'moderator', 'themes', 'summary'):
        value = result.get(field)
        if isinstance(value, dict) and value.get('quote'):
            quotes.append(value['quote'])
    if not quotes:
        return None
    return sum(1 for quote in quotes if quote_in_text(quote, text)) / len(quotes)


def flatten_cited(result):
    if not isinstance(result, dict):
        return result
    flat = {}
    for key, value in result.items():
        flat[key] = value.get('value') if isinstance(value, dict) and 'value' in value else value
    return flat


def score(result, gold, text):
    if not isinstance(result, dict) or result.get('error'):
        return {'error': result.get('error') if isinstance(result, dict) else 'non-dict', 'valid': False}
    result = flatten_cited(result)
    metrics = {'valid': True}
    try:
        metrics['year'] = 1.0 if result.get('year') == gold['year'] else 0.0
    except (TypeError, ValueError):
        metrics['year'] = 0.0
    metrics['title'] = difflib.SequenceMatcher(None, str(result.get('title') or '').lower(),
                                               str(gold['title'] or '').lower()).ratio()
    metrics['venue'] = difflib.SequenceMatcher(None, str(result.get('venue') or '').lower(),
                                               str(gold['venue'] or '').lower()).ratio()
    metrics['speakers'] = set_f1(result.get('speakers'), gold['speakers'])
    metrics['themes'] = theme_f1(result.get('themes'), gold['themes'])
    metrics['summary'] = rouge_l(result.get('summary'), gold['summary'])
    metrics['grounding'] = grounding_rate(result, text)
    metrics['score'] = round(
        0.25 * metrics['year'] + 0.10 * metrics['title'] + 0.05 * metrics['venue']
        + 0.25 * metrics['speakers'] + 0.15 * metrics['themes'] + 0.20 * metrics['summary'], 3)
    return metrics


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='IGF transcript extraction benchmark')
    parser.add_argument('--models', default=','.join(DEFAULT_MODELS))
    parser.add_argument('--methods', default=','.join(DEFAULT_METHODS))
    parser.add_argument('--docs', default='doc_access_2007,doc_closing_2006')
    parser.add_argument('--gold', default=str(HERE / 'gold_labels.json'))
    parser.add_argument('--out', default=str(HERE / 'results'))
    args = parser.parse_args(argv)
    models = [m for m in args.models.split(',') if m]
    methods = [m for m in args.methods.split(',') if m]
    doc_keys = [d for d in args.docs.split(',') if d]

    gold_data = json.load(open(args.gold, encoding='utf-8'))
    window = gold_data['window_chars']
    recovered_paths = sorted(
        Path.cwd().glob('igf_recovered_*/transcripts_recovered.json'),
        key=lambda p: p.stat().st_mtime, reverse=True)
    if not recovered_paths:
        print('no igf_recovered_*/transcripts_recovered.json found in %s' % Path.cwd())
        print('run the transcript recovery step first')
        return 2
    recovered = json.load(open(recovered_paths[0], encoding='utf-8'))
    by_rel = {item['rel_path']: item for item in recovered}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_csv = out_dir / 'metrics.csv'
    raw_file = out_dir / 'raw_results.json'
    previous = []
    done = set()
    if metrics_csv.is_file():
        with open(metrics_csv, encoding='utf-8-sig') as handle:
            previous = list(csv.DictReader(handle))
        for row in previous:
            done.add((row.get('model'), row.get('method'), row.get('doc')))
    raw_rows = json.load(open(raw_file, encoding='utf-8')) if raw_file.is_file() else []

    table = []
    header = ['model', 'method', 'doc', 'score', 'year', 'title', 'venue', 'speakers', 'themes', 'summary', 'grounding', 'latency', 'eval_tokens']

    def save_progress():
        with open(metrics_csv, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=header, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(previous + table)
        with open(raw_file, 'w', encoding='utf-8') as handle:
            json.dump(raw_rows, handle, ensure_ascii=False, indent=1)

    for doc_key in doc_keys:
        gold = gold_data['docs'][doc_key]
        text = by_rel[gold['rel_path']]['text'][:window]
        for model in models:
            for method in methods:
                if (model, method, doc_key) in done:
                    print('[%s | %s | %s] already done, skipping' % (model, method, doc_key), flush=True)
                    continue
                if method == 'rules':
                    if model != models[0]:
                        continue
                    result, stats = run_rules(text, gold['rel_path'])
                else:
                    print('[%s | %s | %s] running...' % (model, method, doc_key), flush=True)
                    result, stats = METHODS[method](model, text)
                metrics = score(result, gold, text)
                metrics.update({
                    'model': model, 'method': method, 'doc': doc_key,
                    'latency': round(stats.get('latency', 0.0), 1),
                    'eval_tokens': stats.get('eval_count'),
                })
                table.append(metrics)
                raw_rows.append({
                    'model': model, 'method': method, 'doc': doc_key,
                    'result': result, 'stats': {k: v for k, v in stats.items() if k != 'latency'},
                    'metrics': metrics,
                })
                if not metrics.get('valid', True):
                    print('   FAILED: %s latency=%ss' % (metrics.get('error'), metrics.get('latency')), flush=True)
                else:
                    print('   year=%.0f title=%.2f venue=%.2f speakers=%.2f themes=%.2f summary=%.2f grounding=%s score=%.3f latency=%ss' % (
                        metrics.get('year', 0.0), metrics.get('title', 0.0), metrics.get('venue', 0.0),
                        metrics.get('speakers', 0.0), metrics.get('themes', 0.0), metrics.get('summary', 0.0),
                        ('%.2f' % metrics['grounding']) if metrics.get('grounding') is not None else 'n/a',
                        metrics.get('score', 0.0), metrics.get('latency')), flush=True)
                save_progress()

    save_progress()

    print('\n== SUMMARY (mean score over docs) ==')
    groups = defaultdict(list)
    for row in table:
        if not row.get('valid'):
            continue
        groups[(row['model'], row['method'])].append(row['score'])
    for key, scores in sorted(groups.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        print('  %-18s %-10s mean=%.3f n=%d' % (key[0], key[1], sum(scores) / len(scores), len(scores)))
    print('\nDone -> %s' % out_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
