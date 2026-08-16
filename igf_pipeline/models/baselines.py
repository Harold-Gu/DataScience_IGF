# -*- coding: utf-8 -*-
"""Keyword-extraction baselines (Model layer).

Three classical baselines used to prove LLM superiority (or its absence):
  * RAKE     Rose, Engel, Cramer & Cowley 2010 (DOI 10.1002/9780470689646.ch1)
  * TextRank Mihalcea & Tarau 2004 (ACL W04-3252)
  * KeyBERT  Grootendorst 2021 (Zenodo 10.5281/zenodo.4461265, software)

All three are implemented with the stdlib only so the pipeline runs anywhere.
KeyBERT tries the optional `keybert` package first; if it is not installed it
falls back to a built-in embedding-free approximation: TF-IDF vectors of
candidate n-grams + cosine similarity + Maximal Marginal Relevance (MMR),
which reproduces the KeyBERT ranking recipe without a transformer model.
"""
import math
import re

try:
    from keybert import KeyBERT as _KeyBERT
except Exception:
    _KeyBERT = None

STOPWORDS = set("""the a an and or but of to in on for with as at by is are was
were be been this that these those it its from we you they he she i not no so
if then than into over under out up down who whom which what when where why how
can could may might must shall should will would do does did have has had about
above after again against all also am an any because before below between both
during each few further here him his her hers more most much myself nor once
only other own same some such too very just s t d m re ve ll don isn aren wasn
weren doesn didn""".split())

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
    """Five lexical similarity metrics (SemEval-2017 Task 10 style, S17-2091)."""
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
    if not p:
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
    """RAKE-style: content phrases between stopwords/punctuation."""
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
    """RAKE: word degree/frequency scores over content phrases (Rose et al. 2010)."""
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
    """TextRank: co-occurrence graph PageRank (Mihalcea & Tarau 2004)."""
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
    """Embedding-free KeyBERT approximation: n-gram TF-IDF vectors + cosine + MMR."""
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
    """KeyBERT via the optional package, else the built-in approximation."""
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
