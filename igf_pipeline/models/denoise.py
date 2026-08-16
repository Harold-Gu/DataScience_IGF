#!/usr/bin/env python3
"""Denoise the extracted IGF JSON corpus.

Removes records clearly unrelated to the IGF (third-party sign-in pages,
Flickr/YouTube shells, ad pages) while keeping every record with any IGF /
meeting signal. Removed records are written to removed.json with a reason
and the triggering evidence.
"""

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter
from datetime import datetime
from pathlib import Path


MEETING_TYPES = {
    'workshop', 'open-forum', 'lightning-talk', 'day-0-event', 'launch-award',
    'networking', 'main-session', 'town-hall', 'transcript', 'report',
    'schedule', 'participants', 'dc-bpf-nri', 'bpf-nri', 'high-level',
    'newcomers', 'news', 'press', 'calls', 'capacity-building',
    'policy-networks', 'donors', 'mag-eg', 'village', 'youth',
    'accessibility', 'about',
}

# Strong, IGF-specific signals.  One hit in title / filename / meta is enough
# to keep a page; two hits inside the body are enough (a single "igf" inside
# a Flickr navigation shell is not real content).
STRONG_RE = re.compile(
    r'\bigf\b|\bigf\d{1,4}\b|internet governance|intgovforum|wsis|multistakeholder|'
    r'dynamic coalition|best practice forum|\bnri\b|\bmag\b|open forum|'
    r'lightning talk|day 0|pre-?event|town ?hall|main session|'
    r'networking session|plenary|workshop|rapporteur|panelist|'
    r'sharm el sheikh', re.I)

# Weak, meeting-document signals (agenda, programme, synthesis paper, ...).
# They are not enough on their own inside a body, but a hit in the filename
# keeps old .txt/.rtf-derived meeting documents whose text the extractor
# could not parse.
WEAK_RE = re.compile(
    r'agenda|programme|program\b|schedule|transcript|proceedings|verbatim|'
    r'speaker|moderator|attendee|registration|venue|consult|synthesis|'
    r'summary|remarks|highlights|questionnaire|orientation|taking stock|'
    r'emerging issues|critical internet resources|regional perspectives|'
    r'opening ceremony|closing ceremony|opening session|closing session|'
    r'roundtable|forum|session|summit|conference|seminar|webinar|meeting|'
    r'report|launch|award|village|exhibition|booth|newsletter|press release|'
    r'call for proposals|panel|dialogue|debate', re.I)

# Third-party brand patterns found in titles of junk pages.  A genuine IGF
# page always ends with "| Internet Governance Forum (IGF)", never with one
# of these brands.  The -of suffix on the URL is irrelevant here.
BRAND_SUFFIX_RE = re.compile(
    r'(?:^\s*|\|\s*|[-–—]\s*)'
    r'(google drive|google docs|google sheets|google forms|zoom|flickr|'
    r'dropbox|youtube|facebook|instagram|linkedin|twitter|pinterest|'
    r'tumblr|slideshare|scribd|prezi|eventbrite)\s*$', re.I)
BRAND_PREFIX_RE = re.compile(
    r'^(google drive|google docs|google sheets|google forms|zoom|flickr|'
    r'youtube|dropbox|facebook|instagram|linkedin)\b', re.I)
YOUTUBE_CONSENT_RE = re.compile(r'before you continue to', re.I)

# Drupal field keys that only exist on structured IGF meeting pages.
DRUPAL_FIELD_RE = re.compile(
    r'theme|subtheme|speaker|organizer|organiser|rapporteur|moderator|'
    r'panelist|co_?organizer|co_?organiser|proposer|policy_?question|'
    r'session_content|key_session|takeaway|call_to_action|sdg|duration|'
    r'format|room|language|participant|issues|outcomes|description', re.I)

# Supporting evidence only: IGF footers are full of "sign in / subscribe",
# so these markers never remove a page by themselves.
AD_MARKER_RE = re.compile(
    r'\b(?:advert|banner|promo|sponsor|casino|gambling|coupon|discount)\b|'
    r'buy now|free trial|retweet|click here', re.I)


def _norm(value):
    return str(value or '')


def _decoded(value):
    try:
        return urllib.parse.unquote(_norm(value))
    except Exception:
        return _norm(value)


def _strong_hits(text):
    return len(STRONG_RE.findall(text or ''))


def _weak_hits(text):
    return len(WEAK_RE.findall(text or ''))


def _drupal_signal(record):
    fields = record.get('drupal_fields') or {}
    if isinstance(fields, dict):
        return any(DRUPAL_FIELD_RE.search(_norm(key)) for key in fields)
    if isinstance(fields, list):
        return any(DRUPAL_FIELD_RE.search(_norm(key)) for key in fields)
    return False


def _meta_signal(record):
    meta = record.get('meta') or {}
    if not isinstance(meta, dict):
        return 0, False
    lowered = {str(k).lower(): v for k, v in meta.items()}
    text = ' '.join(_norm(lowered.get(k)) for k in ('description', 'og:title', 'title'))
    return _strong_hits(text), bool(text.strip())


def _brand_title(title):
    title = _norm(title).strip()
    if not title:
        return False
    if BRAND_SUFFIX_RE.search(title) or BRAND_PREFIX_RE.search(title):
        return True
    return bool(YOUTUBE_CONSENT_RE.search(title))


def assess(record, min_body):
    """Return (keep, reason, evidence) for one record."""
    title = _norm(record.get('title')).strip()
    rel_path = _decoded(record.get('rel_path'))
    body = _norm(record.get('body_text'))
    body_len = len(body.strip())
    rec_type = record.get('type')
    drupal_sig = _drupal_signal(record)
    meta_strong, _ = _meta_signal(record)
    evidence = []

    if _brand_title(title) and not drupal_sig:
        evidence.append('title matches a third-party brand pattern')
        return False, 'third_party_page', evidence

    if drupal_sig:
        return True, '', []

    if _strong_hits(title):
        return True, '', []
    if _strong_hits(rel_path):
        return True, '', []
    if meta_strong:
        return True, '', []
    if _strong_hits(body) >= 2:
        return True, '', []
    if rec_type in MEETING_TYPES:
        return True, '', []

    if _weak_hits(rel_path) or _weak_hits(title):
        return True, '', []
    if _weak_hits(body) >= 3 and body_len >= min_body:
        return True, '', []

    title_lower = title.lower()
    meta = record.get('meta') or {}
    meta_text = ' '.join(_norm(v) for v in meta.values()).lower() if isinstance(meta, dict) else ''
    if AD_MARKER_RE.search(title_lower) or AD_MARKER_RE.search(meta_text):
        evidence.append('ad markers in title/meta')
        return False, 'ad_page', evidence
    if body_len < min_body:
        evidence.append('no IGF signal and empty/tiny body (%d chars)' % body_len)
        return False, 'empty_no_signal', evidence

    evidence.append('no IGF signal, unrelated content (%d body chars)' % body_len)
    return False, 'no_igf_signal', evidence


def _load_records(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get('pages', data.get('records', []))
    return data if isinstance(data, list) else []


def find_latest_input(root):
    candidates = sorted(
        (p for p in Path(root).glob('igf_extracted_*') if (p / 'all.json').is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] / 'all.json' if candidates else None


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Remove non-IGF noise from an extracted all.json')
    parser.add_argument('--input', help='path to all.json (default: newest igf_extracted_*/all.json)')
    parser.add_argument('--output', help='output directory (default: igf_denoised_<timestamp>)')
    parser.add_argument('--min-body', type=int, default=80,
                        help='minimum body length for a signal-less page (default 80)')
    parser.add_argument('--dry-run', action='store_true', help='print stats without writing files')
    args = parser.parse_args(argv)

    input_path = Path(args.input) if args.input else find_latest_input(Path.cwd())
    if input_path is None:
        sys.exit('No input given and no igf_extracted_*/all.json found in %s' % Path.cwd())
    if input_path.is_dir():
        input_path = input_path / 'all.json'
    input_path = input_path.resolve()

    print('INPUT :', input_path)
    raw = _load_records(input_path)
    print('Loaded %d records' % len(raw))

    kept, removed, broken, seen = [], [], 0, set()
    reason_counter, type_kept, type_removed = Counter(), Counter(), Counter()
    empty_kept = 0

    for rec in raw:
        if not isinstance(rec, dict):
            broken += 1
            continue
        rel_path = _norm(rec.get('rel_path'))
        if rel_path and rel_path in seen:
            removed.append(dict(rec, _noise_reason='duplicate', _noise_evidence=['same rel_path']))
            reason_counter['duplicate'] += 1
            continue
        if rel_path:
            seen.add(rel_path)
        keep, reason, evidence = assess(rec, args.min_body)
        if keep:
            if not (_norm(rec.get('body_text')).strip()):
                empty_kept += 1
            kept.append(rec)
            type_kept[_norm(rec.get('type')) or '(none)'] += 1
        else:
            removed.append(dict(rec, _noise_reason=reason, _noise_evidence=evidence))
            reason_counter[reason] += 1
            type_removed[_norm(rec.get('type')) or '(none)'] += 1

    print('\nRESULT: kept=%d removed=%d broken=%d' % (len(kept), len(removed), broken))
    print('Removed by reason:')
    for reason, count in reason_counter.most_common():
        print('  %-20s %d' % (reason, count))
    print('Kept by type:')
    for rec_type, count in sorted(type_kept.items(), key=lambda kv: -kv[1]):
        print('  %-20s %d' % (rec_type, count))
    if type_removed:
        print('Removed by type:')
        for rec_type, count in sorted(type_removed.items(), key=lambda kv: -kv[1]):
            print('  %-20s %d' % (rec_type, count))
    print('Kept records with empty body_text (old .txt/.rtf documents): %d' % empty_kept)
    print('\nRemoved samples:')
    shown = 0
    for reason, _ in reason_counter.most_common():
        for rec in removed:
            if rec.get('_noise_reason') == reason:
                print('  [%s] %s | %s' % (reason, rec.get('title') or '(no title)', rec.get('rel_path')))
                shown += 1
                if shown >= 30:
                    break
        if shown >= 30:
            break

    if args.dry_run:
        print('\nDry run - nothing written.')
        return 0

    out_dir = Path(args.output) if args.output else Path.cwd() / ('igf_denoised_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'all.json', 'w', encoding='utf-8') as handle:
        json.dump(kept, handle, ensure_ascii=False, indent=1)
    with open(out_dir / 'removed.json', 'w', encoding='utf-8') as handle:
        json.dump(removed, handle, ensure_ascii=False, indent=1)
    report_lines = [
        'input: %s' % input_path,
        'total: %d kept: %d removed: %d broken: %d' % (len(raw), len(kept), len(removed), broken),
        'reasons: %s' % dict(reason_counter),
    ]
    for rec in removed:
        report_lines.append('[%s] %s | %s | %s' % (
            rec.get('_noise_reason'), rec.get('title') or '(no title)', rec.get('rel_path'),
            '; '.join(rec.get('_noise_evidence', []))))
    with open(out_dir / 'denoise_report.txt', 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(report_lines))
    print('\nWrote %s (all.json, removed.json, denoise_report.txt)' % out_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
