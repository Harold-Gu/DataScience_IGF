#!/usr/bin/env python3
"""Extraction methods and Ollama client for the transcript benchmark."""

import json
import re
import time
import urllib.request


OLLAMA_CHAT = 'http://127.0.0.1:11434/api/chat'
TIMEOUT = 180

SCHEMA_JSON = '''{
  "title": "string, session title as stated in the document header",
  "year": 2007,
  "venue": "string, city and country",
  "session_type": "string, e.g. main session, closing ceremony, open consultation",
  "speakers": ["SPEAKER NAME"],
  "moderator": "string or null",
  "themes": ["short keyword", "max five"],
  "summary": "one sentence, max 40 words"
}'''

FEWSHOT_EXAMPLE = '''TRANSCRIPT:
Internet Governance Forum Nairobi, Kenya 15 September 2011 Security
>>CHAIR SMITH: Good morning everyone, and welcome to this session.
>>ANNA JONES: Thank you chair. I will speak about cybercrime today.

OUTPUT:
{
  "title": "Internet Governance Forum Nairobi, Kenya 15 September 2011 Security",
  "year": 2011,
  "venue": "Nairobi, Kenya",
  "session_type": "main session",
  "speakers": ["CHAIR SMITH", "ANNA JONES"],
  "moderator": "CHAIR SMITH",
  "themes": ["security", "cybercrime"],
  "summary": "Chair Smith opens the IGF 2011 security session in Nairobi and Anna Jones speaks about cybercrime."
}'''

SYSTEM_PROMPT = (
    'You extract structured metadata from IGF meeting transcripts. '
    'Only use information that is explicitly present in the transcript. '
    'If a field is absent, output null for strings and [] for lists. '
    'Speakers are the names that appear after ">>" at the start of speaker lines. '
    'Respond with valid JSON only, no explanations.'
)


def ollama_chat(model, messages, temperature=0.0, format_json=False, tools=None, timeout=TIMEOUT):
    payload = {
        'model': model,
        'messages': messages,
        'stream': False,
        'options': {'temperature': temperature},
    }
    if model.lower().startswith('qwen3'):
        payload['think'] = False
    if format_json:
        payload['format'] = 'json'
    if tools:
        payload['tools'] = tools
    request = urllib.request.Request(
        OLLAMA_CHAT, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    start = time.time()
    try:
        data = json.load(urllib.request.urlopen(request, timeout=timeout))
    except Exception as error:
        return {'error': '%s: %s' % (type(error).__name__, error)}, {'latency': time.time() - start}
    message = data.get('message') or {}
    stats = {
        'latency': time.time() - start,
        'prompt_eval_count': data.get('prompt_eval_count'),
        'eval_count': data.get('eval_count'),
        'total_duration_ns': data.get('total_duration'),
    }
    return message, stats


def user_prompt(transcript, schema=SCHEMA_JSON):
    return (
        'Extract the session metadata from this transcript into the JSON schema below.\n\n'
        'SCHEMA:\n%s\n\nTRANSCRIPT:\n%s\n\nOUTPUT JSON:' % (schema, transcript)
    )


def _parse_json_content(message):
    tool_calls = message.get('tool_calls') or []
    if tool_calls:
        arguments = tool_calls[0].get('function', {}).get('arguments') or ''
        if isinstance(arguments, dict):
            return arguments
        return json.loads(arguments) if arguments.strip() else {}
    content = message.get('content') or ''
    match = re.search(r'\{.*\}', content, re.S)
    if not match:
        raise ValueError('no JSON object in response: %s' % content[:200])
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise ValueError('invalid JSON: %s' % error)


def run_oneshot(model, transcript):
    message, stats = ollama_chat(
        model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt(transcript)}],
        temperature=0.0, format_json=True)
    if 'error' in message:
        return {'error': message['error']}, stats
    try:
        return _parse_json_content(message), stats
    except ValueError as error:
        return {'error': str(error)}, stats


def run_fewshot(model, transcript):
    prompt = (
        'Here is one worked example.\n\n%s\n\n'
        'Now do the same for this transcript.\n\n%s' % (FEWSHOT_EXAMPLE, user_prompt(transcript))
    )
    message, stats = ollama_chat(
        model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt}],
        temperature=0.0, format_json=True)
    if 'error' in message:
        return {'error': message['error']}, stats
    try:
        return _parse_json_content(message), stats
    except ValueError as error:
        return {'error': str(error)}, stats


FIELD_QUESTIONS = [
    ('title', 'What is the exact title/header line of this session? Answer with the title string only.'),
    ('year', 'In which year did this session take place? Answer with the four digit year only.'),
    ('venue', 'In which city and country did this session take place? Answer "city, country".'),
    ('session_type', 'What kind of session is this (main session, closing ceremony, open consultation, workshop...)?'),
    ('speakers', 'List every speaker name that appears after ">>" in this transcript as a JSON array of strings.'),
    ('moderator', 'Who moderates/chairs the session? If unclear answer null.'),
    ('themes', 'List up to five short keyword themes discussed, as a JSON array of strings.'),
    ('summary', 'Summarize this transcript in one sentence of at most 40 words.'),
]


def run_fieldqa(model, transcript):
    fields = {}
    stats_total = {'latency': 0.0}
    for key, question in FIELD_QUESTIONS:
        message, stats = ollama_chat(
            model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': 'TRANSCRIPT:\n%s\n\n%s\nAnswer with JSON: {"answer": ...}' % (transcript, question)}],
            temperature=0.0, format_json=True)
        stats_total['latency'] += stats.get('latency', 0.0)
        stats_total.setdefault('eval_count', 0)
        stats_total['eval_count'] += stats.get('eval_count') or 0
        if 'error' in message:
            fields[key] = None
            continue
        try:
            answer = _parse_json_content(message).get('answer')
        except ValueError:
            answer = None
        if key == 'speakers' and isinstance(answer, str):
            answer = [part.strip() for part in re.split(r'[,;]', answer) if part.strip()]
        fields[key] = answer
    return fields, stats_total


TOOLS_DEFINITION = [{
    'type': 'function',
    'function': {
        'name': 'extract_session',
        'description': 'Store structured metadata extracted from an IGF transcript',
        'parameters': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string'},
                'year': {'type': 'integer'},
                'venue': {'type': 'string'},
                'session_type': {'type': 'string'},
                'speakers': {'type': 'array', 'items': {'type': 'string'}},
                'moderator': {'type': ['string', 'null']},
                'themes': {'type': 'array', 'items': {'type': 'string'}},
                'summary': {'type': 'string'},
            },
            'required': ['title', 'year', 'venue', 'session_type', 'speakers', 'moderator', 'themes', 'summary'],
        },
    },
}]


def run_tools(model, transcript):
    message, stats = ollama_chat(
        model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': 'TRANSCRIPT:\n%s' % transcript}],
        temperature=0.0, tools=TOOLS_DEFINITION)
    if 'error' in message:
        return {'error': message['error']}, stats
    try:
        return _parse_json_content(message), stats
    except ValueError as error:
        return {'error': str(error)}, stats


CITED_SCHEMA = '''{
  "title": {"value": "string", "quote": "exact verbatim substring from the transcript"},
  "year": {"value": 2007, "quote": "verbatim substring"},
  "venue": {"value": "string", "quote": "verbatim substring"},
  "session_type": {"value": "string", "quote": "verbatim substring"},
  "speakers": {"value": ["NAME"], "quote": "verbatim substring"},
  "moderator": {"value": "string or null", "quote": "verbatim substring or null"},
  "themes": {"value": ["keyword"], "quote": "verbatim substring"},
  "summary": {"value": "one sentence", "quote": "verbatim substring"}
}'''


def run_cited(model, transcript):
    prompt = (
        'Extract the metadata AND for every field give the exact verbatim substring of the '
        'transcript that supports it (for summary quote the sentence you based it on).\n\n'
        'SCHEMA:\n%s\n\nTRANSCRIPT:\n%s\n\nOUTPUT JSON:' % (CITED_SCHEMA, transcript)
    )
    message, stats = ollama_chat(
        model, [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt}],
        temperature=0.0, format_json=True)
    if 'error' in message:
        return {'error': message['error']}, stats
    try:
        return _parse_json_content(message), stats
    except ValueError as error:
        return {'error': str(error)}, stats


def run_chunked(model, transcript, chunk=2500, overlap=200):
    chunks, start = [], 0
    while start < len(transcript):
        chunks.append(transcript[start:start + chunk])
        if start + chunk >= len(transcript):
            break
        start += chunk - overlap
    parts = []
    stats_total = {'latency': 0.0}
    for index, chunk_text in enumerate(chunks):
        result, stats = run_oneshot(model, chunk_text)
        stats_total['latency'] += stats.get('latency', 0.0)
        stats_total.setdefault('eval_count', 0)
        stats_total['eval_count'] += stats.get('eval_count') or 0
        parts.append((index, result))
    if not parts:
        return {'error': 'no chunks'}, stats_total
    merged = {}
    for key in ('title', 'year', 'venue', 'session_type', 'moderator'):
        values = [p[1].get(key) for p in parts if isinstance(p[1], dict) and p[1].get(key) not in (None, '', [])]
        if values:
            merged[key] = values[0]
    speakers, themes = [], []
    for _, part in parts:
        if isinstance(part, dict):
            speakers.extend([s for s in (part.get('speakers') or []) if isinstance(s, str)])
            themes.extend([t for t in (part.get('themes') or []) if isinstance(t, str)])
    merged['speakers'] = list(dict.fromkeys(speakers))
    merged['themes'] = list(dict.fromkeys(themes))
    summaries = [p[1].get('summary') for p in parts if isinstance(p[1], dict) and p[1].get('summary')]
    merged['summary'] = ' '.join(str(s) for s in summaries)[:600]
    errors = [p[1].get('error') for p in parts if isinstance(p[1], dict) and p[1].get('error')]
    if errors:
        merged['error'] = '; '.join(errors)
    return merged, stats_total


SPEAKER_LINE_RE = re.compile(r'^\s*(?:>{1,3}\s*)?([A-Z][A-Z0-9 .&\'\-]{2,50}):\s*(.*)$')
SPEAKER_STOPWORDS = {'NOTE', 'MODERATOR', 'CHAIR', 'CHAIRMAN', 'SPEAKER', 'AUDIENCE', 'APPLAUSE', 'LAUGHTER', 'MUSIC', 'GOOD'}
THEME_DICT = {
    'access': ['access', 'digital divide', 'infrastructure'],
    'security': ['security', 'cybercrime', 'privacy'],
    'governance': ['internet governance', 'multistakeholder'],
    'consultation': ['taking stock', 'consultation', 'advisory group', 'way forward'],
}


def run_rules(transcript, rel_path=''):
    speakers = []
    for line in transcript.splitlines():
        match = SPEAKER_LINE_RE.match(line)
        if match and match.group(1).strip().upper() not in SPEAKER_STOPWORDS:
            name = match.group(1).strip()
            if name not in speakers:
                speakers.append(name)
    year_match = re.search(r'\b(19|20)\d{2}\b', transcript[:200])
    low = transcript[:6000].lower()
    themes = []
    for _, keywords in THEME_DICT.items():
        for keyword in keywords:
            if keyword in low and keyword not in themes:
                themes.append(keyword)
    return {
        'title': re.sub(r'\s+', ' ', transcript.splitlines()[0]).strip() if transcript else '',
        'year': int(year_match.group(0)) if year_match else None,
        'venue': '',
        'session_type': '',
        'speakers': speakers,
        'moderator': speakers[0] if speakers else None,
        'themes': themes[:5],
        'summary': '',
    }, {'latency': 0.0}


METHODS = {
    'rules': run_rules,
    'oneshot': run_oneshot,
    'fewshot': run_fewshot,
    'fieldqa': run_fieldqa,
    'tools': run_tools,
    'cited': run_cited,
    'chunked': run_chunked,
}
