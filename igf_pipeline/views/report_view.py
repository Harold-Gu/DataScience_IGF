"""Validation report rendering (View layer): quality tables,
directory breakdown and Drupal field analysis output."""


SEP = "======================================================================"

SEP2 = "----------------------------------------------------------------------"

def sp(s):
    try: return str(s)
    except: return repr(s)

def print_quality(label, counts, size_dist, body_len_dist, type_stats, bad_files):
    good = counts["ok"]; bad = sum(v for k,v in counts.items() if k!="ok"); total = good+bad
    print("\n  " + label + ": {} files, {} OK ({:.1f}%)".format(total, good, good/max(total,1)*100))
    print("\n  " + SEP2 + "\n  QUALITY FLAGS\n  " + SEP2)
    for l,key in [("Valid","ok"),("Empty (<300B)","empty"),("Tags-only","tags_only"),
        ("JS-only","js_only"),("Cloudflare","cloudflare"),("Access denied","access_denied"),
        ("Bad encoding","bad_enc"),("Replacement chars","repl"),("Read error","rerr")]:
        print("  {:<30s} {:>6d}".format(l, counts[key]))
    print("\n  " + SEP2 + "\n  SIZE DISTRIBUTION\n  " + SEP2)
    for k in ["<100B","100-500B","500B-2KB","2-10KB","10-50KB","50-200KB",">200KB"]:
        bar = "#"*max(1,size_dist[k]//max(1,total//50))
        print("  {:<12s} {:>5d}  {}".format(k, size_dist[k], bar))
    print("\n  " + SEP2 + "\n  BODY TEXT LENGTH\n  " + SEP2)
    for k in ["<100","100-500","500-2K","2-10K","10-50K",">50K"]:
        bar = "#"*max(1,body_len_dist[k]//max(1,total//50))
        print("  {:<12s} {:>5d}  {}".format(k, body_len_dist[k], bar))

def print_type_table(type_stats):
    print("\n  " + SEP2 + "\n  DIRECTORY BREAKDOWN\n  " + SEP2)
    print("  {:<25s} {:>6s} {:>5s} {:>6s} {:>8s} {:>8s} {:>7s}".format(
        "Directory","Files","Bad","Bad%","AvgSize","AvgBody","Drupal%"))
    for t in sorted(type_stats.keys(),key=lambda k:-type_stats[k]["total"]):
        s = type_stats[t]; n = s["total"]; bn = s["bad"]; bp = bn/max(n,1)*100
        vn = max(n-bn,1); asize = s["total_size"]/max(n,1); abody = s["total_body"]/vn
        dp = s["drupal_count"]/vn*100
        flag = " !!!" if bp>50 else " !" if bp>20 else ""
        print("  {:<25s} {:>6d} {:>5d} {:>5.0f}%{} {:>7.1f}KB {:>7.0f}c {:>6.1f}%".format(
            t, n, bn, bp, flag, asize/1024, abody, dp))

TYPE_DESCRIPTIONS = {
    "workshop": {"family":"Family A (no-suffix)","note":"body(2010-16) -> session-content(2017+)","key_fields":["session-content","theme","speakers","policy-questions","sdgs","co-organizers","discussion-facilitation"]},
    "open-forum": {"family":"Family B (-of suffix)","note":"ITU/UNESCO/OECD. -of = Open Forum specific","key_fields":["description-of","theme-of","organizers-of","speakers-of","rapporteur-of","report"]},

    "day-0-event": {"family":"Mixed (A+C)","note":"Pre-events. Light Drupal, body is primary","key_fields":["description","description-0","organizers","organizers-0"]},
    "launch-award": {"family":"Mixed","note":"Report launches + awards","key_fields":["description","description-0","organizers","speakers","report"]},
    "networking": {"family":"Family C (-0 suffix)","note":"Informal. Similar to Lightning Talks","key_fields":["description-0","organizers-0","theme-0","format-0","duration-0"]},
    "main-session": {"family":"Mixed","note":"Plenary/high-level. Sparse Drupal","key_fields":["description","speakers","theme","organizers"]},
    "town-hall": {"family":"Mixed","note":"Open discussions","key_fields":["description","organizers","speakers","format"]},
    "report": {"family":"N/A","note":"Post-session reports","key_fields":["report","body","description"]},
    "transcript": {"family":"N/A","note":"Verbatim transcripts","key_fields":["body","description"]},
    "schedule": {"family":"N/A","note":"Schedules/agendas","key_fields":["body","description"]},
    "participants": {"family":"N/A","note":"indico.un.org","key_fields":["body"]},
    "dc-bpf-nri": {"family":"Mixed","note":"DC/BPF/NRI intersessional","key_fields":["description","organizers","theme","report"]},
}

def analyze_drupal(type_stats, drupal_fields_by_type, drupal_labels_by_type):
    print("\n" + SEP + "\n  PART 2: DRUPAL FIELD ANALYSIS (classified types only)\n" + SEP)
    for ptype in sorted(drupal_fields_by_type.keys(), key=lambda k: -sum(drupal_fields_by_type[k].values())):
        fields = drupal_fields_by_type[ptype]; labels = drupal_labels_by_type[ptype]
        if not fields or sum(fields.values()) < 10: continue
        ts = type_stats.get(ptype,{}); n_pages = ts.get("total",0)-ts.get("bad",0)
        drupal_pages = ts.get("drupal_count",0)
        desc = TYPE_DESCRIPTIONS.get(ptype,{"family":"?","note":"","key_fields":[]})
        print("\n  [{}]  {}  |  {} pages ({:.0f}% Drupal)  |  {} fields, {} unique".format(
            ptype.upper(), desc.get('family','?'), drupal_pages, drupal_pages/max(n_pages,1)*100,
            sum(fields.values()), len(fields)))
        print("    " + desc.get('note',''))
        for fn, cnt in fields.most_common(10):
            pct = cnt/max(n_pages,1)*100
            print("    field_{:<45s} {:>5d} ({:>5.0f}%) {}".format(fn, cnt, pct, "#"*max(1,int(pct/5))))
        if labels:
            parts = ["[{}]{}".format(sp(lb),cn) for lb,cn in labels.most_common(5)]
            print("    Labels: " + " | ".join(parts))
