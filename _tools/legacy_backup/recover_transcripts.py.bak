#!/usr/bin/env python3
"""Recover the raw text of the old IGF verbatim transcripts.

The denoised corpus keeps ~55 records whose JSON body_text is empty even
though real content exists on disk: the files are real-time captioning
transcripts saved as .txt/.rtf with an .html suffix, which the HTML extractor
could not parse.  This script:
  - locates each file under the newest igf_classified_*/ directory,
  - decodes plain text / raw RTF / minimal HTML,
  - splits the captioning into speaker turns (">>NAME: text"),
  - writes transcripts_recovered.json and a merged all_recovered.json
    (the original corpus with body_text filled back in).
"""

import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path


SPEAKER_RE = re.compile(r'^\s*(?:>{1,3}\s*)?([A-Z][A-Z0-9 .&\'\-]{2,50}):\s*(.*)$')
SPEAKER_BLACKLIST = {
    'NOTE', 'NOTES', 'MODERATOR', 'CHAIR', 'CHAIRMAN', 'CHAIRPERSON', 'SPEAKER',
    'SPEAKERS', 'AUDIENCE', 'QUESTION', 'QUESTIONS', 'ANSWER', 'ANSWERS',
    'COMMENTS', 'REMARK', 'REMARKS', 'THANK', 'THANKS', 'NEXT', 'OKAY', 'OK',
    'MUSIC', 'APPLAUSE', 'LAUGHTER', 'AGENDA', 'TIME', 'DOCUMENT', 'DOCUMENTS',
    'RESOLUTION', 'CONCLUSION', 'CONCLUSIONS', 'SESSION', 'MORNING', 'AFTERNOON',
    'EVENING', 'DEBATE', 'DISCUSSION', 'INTERVENTION', 'INTERVENTIONS', 'REPORT',
    'REPORTS', 'RECOMMENDATION', 'RECOMMENDATIONS', 'STATEMENT', 'STATEMENTS',
    'TEXT', 'ANNEX', 'ANNEXES', 'SUMMARY', 'INTRODUCTION', 'BACKGROUND',
    'CONTEXT', 'OBJECTIVES', 'OUTCOME', 'OUTCOMES', 'FOLLOW', 'UP', 'END',
    'FINAL', 'POINTS', 'POINT', 'PROPOSAL', 'PROPOSALS', 'OTHERS', 'SIDE',
    'PANEL', 'PANELISTS', 'FACILITATOR', 'FACILITATORS', 'RAPPORTEUR', 'CO',
    'CHAIRS', 'SECRETARIAT', 'LADIES', 'GENTLEMEN', 'GOOD', 'WELCOME',
}


def log(*args):
    print(*args)


def decode_bytes(raw):
    """Best-effort byte -> str decoding, minimizing replacement characters."""
    candidates = []
    for encoding in ('utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidates.append((text.count('\ufffd'), encoding, text))
    if not candidates:
        return raw.decode('latin-1', errors='replace')
    candidates.sort(key=lambda item: item[0])
    return candidates[0][2]


def rtf_to_text(raw):
    """Minimal RTF reader: control words, hex escapes, group skipping."""
    text = decode_bytes(raw)
    out = []
    skip_depth = None
    depth = 0
    i, n = 0, len(text)
    SKIP_GROUPS = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict',
                   'object', 'field', 'header', 'footer', 'footerf',
                   'nonestdpictures', 'nonshppict'}
    while i < n:
        ch = text[i]
        if ch == '\\':
            if i + 1 < n and text[i + 1] == "'":
                try:
                    out.append(chr(int(text[i + 2:i + 4], 16)))
                except ValueError:
                    pass
                i += 4
                continue
            if i + 1 < n and text[i + 1] == '*':
                i += 2
                continue
            match = re.match(r'([a-zA-Z]+)(-?\d+)? ?', text[i + 1:])
            if match:
                word = match.group(1).lower()
                if word in ('par', 'line'):
                    out.append('\n')
                elif word == 'tab':
                    out.append('\t')
                elif word in ('emdash', 'endash'):
                    out.append('-')
                elif word == 'lquote':
                    out.append("'")
                elif word == 'rquote':
                    out.append("'")
                elif word == 'ldblquote':
                    out.append('"')
                elif word == 'rdblquote':
                    out.append('"')
                elif word == 'u':
                    num = match.group(2)
                    if num:
                        try:
                            out.append(chr(int(num)))
                        except ValueError:
                            pass
                consumed = 1 + match.end()
                if word == 'bin' and match.group(2):
                    consumed += int(match.group(2))
                i += consumed
                continue
            if i + 1 < n and text[i + 1] in '\\{}-_~|':
                out.append({'\\': '\\', '{': '{', '}': '}', '-': '-', '_': '-', '~': ' ', '|': ' '}[text[i + 1]])
                i += 2
                continue
            i += 1
            continue
        if ch == '{':
            depth += 1
            if skip_depth is None:
                match = re.match(r'\{\\(\*)?([a-zA-Z]+)', text[i:i + 64])
                if match and (match.group(1) or match.group(2).lower() in SKIP_GROUPS):
                    skip_depth = depth
            i += 1
            continue
        if ch == '}':
            if skip_depth == depth:
                skip_depth = None
            depth = max(0, depth - 1)
            i += 1
            continue
        if skip_depth is None:
            out.append(ch)
        i += 1
    return ''.join(out)


def html_to_text(raw):
    text = decode_bytes(raw)
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    return html.unescape(text)


def looks_like_html(raw):
    sample = raw[:4000]
    return sample.count(b'<') > sample.count(b'>') * 0.5 and b'<' in sample


def is_php_error_page(raw):
    sample = raw[:4000].lower()
    return b'deprecated' in sample and (b'.php' in sample or b'joomla' in sample)


def parse_file(path):
    raw = path.read_bytes()
    if raw.lstrip()[:5] == b'{\\rtf':
        return rtf_to_text(raw), 'rtf'
    if is_php_error_page(raw):
        return '', 'php_error_page'
    if looks_like_html(raw):
        return html_to_text(raw), 'html'
    return decode_bytes(raw), 'text'


def speaker_turns(text):
    """Split captioning transcript into (speaker, text) turns."""
    turns = []
    current = None
    for line in text.splitlines():
        match = SPEAKER_RE.match(line)
        if match and match.group(1).strip().upper() not in SPEAKER_BLACKLIST:
            speaker = match.group(1).strip()
            if current is None or current[0] != speaker:
                current = [speaker, match.group(2).strip()]
                turns.append(current)
            else:
                current[1] += ' ' + match.group(2).strip()
        else:
            if current is not None and line.strip():
                current[1] += ' ' + line.strip()
    return [{'speaker': s, 'text': t} for s, t in turns if t]


def normalize(text):
    text = text.replace('\x00', '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def locate_file(record, root):
    rel_path = (record.get('rel_path') or '').replace('/', '\\')
    candidates = []
    if rel_path:
        candidates.append(root / rel_path)
    folder = record.get('folder')
    file_name = record.get('file')
    if folder and file_name:
        candidates.append(root / str(folder).replace('/', '\\') / str(file_name))
    for path in candidates:
        if path.is_file():
            return path
    return None


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Recover old verbatim transcripts into the JSON corpus')
    parser.add_argument('--input', help='denoised all.json (default: newest igf_denoised_*/all.json)')
    parser.add_argument('--base', help='classified base directory (default: newest igf_classified_*)')
    parser.add_argument('--output', help='output directory (default: igf_recovered_<timestamp>)')
    args = parser.parse_args(argv)

    if args.input:
        input_path = Path(args.input).resolve()
    else:
        candidates = sorted(Path.cwd().glob('igf_denoised_*/all.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            sys.exit('no igf_denoised_*/all.json found; use --input')
        input_path = candidates[0]
    records = json.load(open(input_path, encoding='utf-8'))
    log('INPUT :', input_path, '| records:', len(records))

    if args.base:
        base_dirs = [Path(args.base)]
    else:
        base_dirs = sorted(Path.cwd().glob('igf_classified_*'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not base_dirs:
        sys.exit('no classified base directory found; use --base')

    targets = [r for r in records if not (r.get('body_text') or '').strip()]
    log('Empty-body targets:', len(targets))

    recovered, results = [], []
    status_counts = {}
    for record in targets:
        path = None
        for base in base_dirs:
            path = locate_file(record, base)
            if path:
                break
        if path is None:
            results.append((record, 'missing', 0, 0))
            status_counts['missing'] = status_counts.get('missing', 0) + 1
            continue
        text, kind = parse_file(path)
        text = normalize(text)
        turns = speaker_turns(text) if kind in ('text', 'rtf') else []
        status = kind if text else kind + '_empty'
        status_counts[status] = status_counts.get(status, 0) + 1
        results.append((record, status, len(text), len(turns)))
        if text:
            recovered.append({
                'rel_path': record.get('rel_path'),
                'file': record.get('file'),
                'source': str(path),
                'kind': kind,
                'chars': len(text),
                'speaker_turn_count': len(turns),
                'speaker_turns': turns,
                'text': text,
            })

    log('\nRecovery status:')
    for status, count in sorted(status_counts.items()):
        log('  %-20s %d' % (status, count))
    log('\nRecovered files:')
    for record, status, chars, turns in results:
        if chars:
            log('  [ok %-6d chars, %3d turns] %s' % (chars, turns, record.get('rel_path')))
        else:
            log('  [%s] %s' % (status, record.get('rel_path')))

    out_dir = Path(args.output) if args.output else Path.cwd() / ('igf_recovered_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'transcripts_recovered.json', 'w', encoding='utf-8') as handle:
        json.dump(recovered, handle, ensure_ascii=False, indent=1)

    merged = []
    by_rel = {item['rel_path']: item for item in recovered}
    filled = 0
    for record in records:
        item = by_rel.get(record.get('rel_path'))
        if item:
            record = dict(record)
            record['body_text'] = item['text']
            record['recovered_from_disk'] = True
            record['speaker_turns'] = item['speaker_turns']
            record['recovery_kind'] = item['kind']
            filled += 1
        merged.append(record)
    with open(out_dir / 'all_recovered.json', 'w', encoding='utf-8') as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=1)

    speaker_counter = {}
    for item in recovered:
        for turn in item['speaker_turns']:
            speaker_counter[turn['speaker']] = speaker_counter.get(turn['speaker'], 0) + 1
    if speaker_counter:
        log('\nTop speakers in recovered transcripts:')
        for speaker, count in sorted(speaker_counter.items(), key=lambda kv: -kv[1])[:15]:
            log('  %-40s %d turns' % (speaker[:40], count))

    with open(out_dir / 'recovery_report.txt', 'w', encoding='utf-8') as handle:
        for record, status, chars, turns in results:
            handle.write('[%-16s] chars=%-7d turns=%-4d %s\n' % (status, chars, turns, record.get('rel_path')))
    log('\nWrote %s (transcripts_recovered.json, all_recovered.json, recovery_report.txt)' % out_dir)
    log('Records with body_text filled: %d' % filled)
    return 0


if __name__ == '__main__':
    sys.exit(main())
