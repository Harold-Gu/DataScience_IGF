#!/usr/bin/env python3
"""Black-box verification of LLM extraction outputs.

Four independent checks (none of them trusts the model):
  1. grounding     - every cited quote must appear in the source transcript
  2. self-consistency - same model+prompt at temperature 0.7, 3 runs; low
                       agreement between runs flags instability
  3. cross-model   - agreement between different models on the same document;
                       fields where models disagree are flagged for human review
  4. negative tests - documents that contain no session info; a good extractor
                       must output null/empty instead of hallucinating
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from extractors import METHODS, ollama_chat, SYSTEM_PROMPT, FIELD_QUESTIONS
from benchmark import normalize_name, set_f1, theme_f1, quote_in_text


HERE = Path(__file__).resolve().parent

NEGATIVE_TESTS = {
    'no_session_at_all': (
        'The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. '
        'How vexingly quick daft zebras jump. Bright vixens jump; dozy fowl quack.',
        'a text with no session information'),
    'trap_quantum': (
        '>>MARKUS KUMMER: Good morning, ladies and gentlemen. May I ask you to be seated. '
        'We would like to start with the session on internet access and infrastructure.',
        'a session that never mentions quantum'),
}


def _flatten_cited(result):
    """cited-schema result -> flat dict plus a list of (field, quote) pairs."""
    if not isinstance(result, dict):
        return {}, []
    flat = {}
    quotes = []
    for key, value in result.items():
        if isinstance(value, dict) and 'value' in value:
            flat[key] = value.get('value')
            if value.get('quote'):
                quotes.append((key, value['quote']))
        else:
            flat[key] = value
    return flat, quotes


def check_grounding(result, text):
    flat, quotes = _flatten_cited(result)
    if not quotes:
        return None
    passed = sum(1 for _, quote in quotes if quote_in_text(quote, text))
    return {
        'grounding_rate': passed / len(quotes),
        'total_quotes': len(quotes),
        'failed': [{'field': f, 'quote': q[:120]} for f, q in quotes if not quote_in_text(q, text)],
    }


def check_self_consistency(model, method, text, runs=3):
    outputs = []
    for _ in range(runs):
        if method == 'fieldqa':
            fields = {}
            for key, question in FIELD_QUESTIONS:
                message, _ = ollama_chat(
                    model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                            {'role': 'user', 'content': 'TRANSCRIPT:\n%s\n\n%s\nAnswer with JSON: {"answer": ...}' % (text, question)}],
                    temperature=0.7, format_json=True)
                try:
                    match = re.search(r'\{.*\}', message.get('content') or '', re.S)
                    fields[key] = json.loads(match.group(0)).get('answer') if match else None
                except (json.JSONDecodeError, AttributeError, ValueError):
                    fields[key] = None
            outputs.append(fields)
        else:
            message, _ = ollama_chat(
                model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': 'Extract speakers, themes and summary from this transcript as JSON.'}],
                temperature=0.7, format_json=True)
            try:
                match = re.search(r'\{.*\}', message.get('content') or '', re.S)
                outputs.append(json.loads(match.group(0)) if match else {})
            except (json.JSONDecodeError, ValueError):
                outputs.append({})
    def pairwise(field):
        scores = []
        for i in range(len(outputs)):
            for j in range(i + 1, len(outputs)):
                left, right = outputs[i].get(field), outputs[j].get(field)
                if isinstance(left, list) and isinstance(right, list):
                    scores.append(set_f1(left, right))
                else:
                    scores.append(1.0 if str(left or '') == str(right or '') else 0.0)
        return sum(scores) / len(scores) if scores else 1.0
    return {'runs': runs, 'speakers': pairwise('speakers'), 'themes': pairwise('themes'), 'summary': pairwise('summary')}


def check_cross_model(models, doc_texts, method='oneshot'):
    """One document, several models: agreement matrix on speakers/themes."""
    results = {}
    for model in models:
        print('[cross-model] %s' % model, flush=True)
        result, _ = METHODS[method](model, doc_texts[0])
        results[model] = result if isinstance(result, dict) and not result.get('error') else {}
    matrix = {}
    for left in models:
        for right in models:
            if left < right:
                matrix['%s vs %s' % (left, right)] = {
                    'speakers': set_f1(results.get(left, {}).get('speakers'), results.get(right, {}).get('speakers')),
                    'themes': theme_f1(results.get(left, {}).get('themes'), results.get(right, {}).get('themes')),
                }
    return {'agreement': matrix, 'outputs': results}


def run_negative_tests(model, method='fieldqa'):
    outcomes = {}
    for name, (text, label) in NEGATIVE_TESTS.items():
        result, _ = METHODS[method](model, text)
        flat = {}
        if isinstance(result, dict):
            for key, value in result.items():
                flat[key] = value.get('value') if isinstance(value, dict) else value
        nonempty = {k: v for k, v in flat.items() if v not in (None, '', [], {}, 'null')}
        if name == 'trap_quantum':
            quantum = nonempty.get('themes') or nonempty.get('summary') or ''
            hallucinated = any('quantum' in str(x).lower() for x in (quantum if isinstance(quantum, list) else [quantum]))
            outcomes[name] = {'label': label, 'nonempty_fields': sorted(nonempty), 'quantum_hallucinated': bool(hallucinated)}
        else:
            outcomes[name] = {'label': label, 'nonempty_fields': sorted(nonempty),
                              'hallucination_count': len(nonempty)}
    return outcomes


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Verify LLM extraction outputs')
    parser.add_argument('--raw', default=str(HERE / 'results' / 'raw_results.json'))
    parser.add_argument('--models', default='qwen3.5:9b,qwen3:8b,qwen3.5:4b,qwen2.5:latest')
    parser.add_argument('--method', default='cited')
    parser.add_argument('--self-consistency', action='store_true')
    parser.add_argument('--cross-model', action='store_true')
    parser.add_argument('--negatives', action='store_true')
    parser.add_argument('--doc', default='doc_access_2007')
    args = parser.parse_args(argv)

    gold_data = json.load(open(HERE / 'gold_labels.json', encoding='utf-8'))
    window = gold_data['window_chars']
    recovered = json.load(open(
        sorted(Path.cwd().glob('igf_recovered_*/transcripts_recovered.json'),
               key=lambda p: p.stat().st_mtime, reverse=True)[0], encoding='utf-8'))
    by_rel = {item['rel_path']: item for item in recovered}
    doc = gold_data['docs'][args.doc]
    text = by_rel[doc['rel_path']]['text'][:window]

    report = {}

    raw_path = Path(args.raw)
    if raw_path.is_file():
        raw_rows = json.load(open(raw_path, encoding='utf-8'))
        cited_rows = [r for r in raw_rows if r['method'] == 'cited' and r['doc'] == args.doc]
        print('== GROUNDING (cited runs from benchmark) ==')
        for row in cited_rows:
            result = row['result']
            check = check_grounding(result, text)
            report['grounding_%s' % row['model']] = check
            if check:
                print('  %-18s rate=%.2f failed=%d' % (row['model'], check['grounding_rate'], len(check['failed'])))
                for item in check['failed'][:3]:
                    print('      FAIL %s: %r' % (item['field'], item['quote']))
            else:
                print('  %-18s no citations found' % row['model'])

    models = [m for m in args.models.split(',') if m]
    if args.self_consistency:
        print('\n== SELF-CONSISTENCY (temperature 0.7, 3 runs) ==')
        for model in models:
            result = check_self_consistency(model, args.method, text)
            report['self_consistency_%s' % model] = result
            print('  %-18s speakers=%.2f themes=%.2f summary=%.2f' % (
                model, result['speakers'], result['themes'], result['summary']))

    if args.cross_model:
        print('\n== CROSS-MODEL AGREEMENT ==')
        result = check_cross_model(models, [text], method='oneshot')
        report['cross_model'] = result['agreement']
        for pair, scores in result['agreement'].items():
            print('  %-45s speakers=%.2f themes=%.2f' % (pair, scores['speakers'], scores['themes']))

    if args.negatives:
        print('\n== NEGATIVE TESTS (hallucination traps) ==')
        for model in models[:1]:
            outcomes = run_negative_tests(model, method='fieldqa')
            report['negatives_%s' % model] = outcomes
            for name, outcome in outcomes.items():
                print('  [%s] %-18s nonempty=%s quantum_hallucinated=%s' % (
                    name, outcome['label'][:18], outcome.get('nonempty_fields'),
                    outcome.get('quantum_hallucinated')))

    out = HERE / 'results' / 'verification_report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(report, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\nVerification report -> %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
