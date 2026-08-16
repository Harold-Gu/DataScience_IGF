"""Validation-report controller: orchestrates scan (model) + rendering (view).
Equivalent to the former report_scrape.py main()."""
import os
import time
import argparse

from ..models import validation as model
from ..views import report_view as view


def main(argv=None):
    p = argparse.ArgumentParser(description="IGF Validation Report")
    p.add_argument("--full", default=None)
    p.add_argument("--classified", default=None)
    p.add_argument("--extracted", default=None)
    p.add_argument("--no-drupal", action="store_true")
    args = p.parse_args(argv)
    cwd = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

    def _pick(pattern):
        dirs = [d for d in os.listdir(cwd) if d.startswith(pattern) and os.path.isdir(os.path.join(cwd, d))]
        if not dirs:
            return None
        dirs.sort(reverse=True)
        return os.path.join(cwd, dirs[0])

    if not args.full:
        args.full = _pick("igf_full_")
    if not args.classified:
        args.classified = _pick("igf_classified_")
    if not args.extracted:
        args.extracted = _pick("igf_extracted_")

    print("\n" + "#" * 70 + "\n  IGF SCRAPE VALIDATION REPORT\n  Time: " + time.strftime('%Y-%m-%d %H:%M:%S'))
    print("  Full:       " + (args.full or 'N/A'))
    print("  Classified: " + (args.classified or 'N/A'))
    print("  Extracted:  " + (args.extracted or 'N/A') + "\n" + "#" * 70)

    if args.full and os.path.isdir(args.full):
        print("\n" + view.SEP + "\n  PART 1a: FULL SCRAPE\n  Source: " + args.full + "\n" + view.SEP)
        c_full, bf_full, sz_full, bl_full, ts_full, df_full, dl_full = model.scan_html(args.full)
        view.print_quality("FULL SCRAPE", c_full, sz_full, bl_full, ts_full, bf_full)
        view.print_type_table(ts_full)
        model.validate_documents(args.full)

    if args.classified and os.path.isdir(args.classified):
        print("\n" + view.SEP + "\n  PART 1b: CLASSIFIED\n  Source: " + args.classified + "\n" + view.SEP)
        c_cls, bf_cls, sz_cls, bl_cls, ts_cls, df_cls, dl_cls = model.scan_html(args.classified)
        view.print_quality("CLASSIFIED", c_cls, sz_cls, bl_cls, ts_cls, bf_cls)
        view.print_type_table(ts_cls)

        if args.full and os.path.isdir(args.full):
            full_total = sum(v for k, v in c_full.items() if k != "rerr")
            cls_total = sum(v for k, v in c_cls.items() if k != "rerr")
            gap = full_total - cls_total
            print("\n  " + view.SEP2 + "\n  GAP: FULL -> CLASSIFIED\n  " + view.SEP2)
            print("  Full scrape:   {} files".format(full_total))
            print("  Classified:    {} files".format(cls_total))
            print("  Lost:          {} files ({:.1f}%)".format(gap, gap / max(full_total, 1) * 100))
            print("  (dedup by content hash + <300B dropped + _invalid pages)")

        if not args.no_drupal:
            view.analyze_drupal(ts_cls, df_cls, dl_cls)

        if any(bf_cls.values()):
            print("\n  " + view.SEP2 + "\n  BAD FILES (classified)\n  " + view.SEP2)
            for cat in ["empty", "tags_only", "js_only", "cloudflare", "access_denied", "bad_enc", "repl"]:
                if bf_cls[cat]:
                    files = bf_cls[cat][:5]
                    more = " ..." if len(bf_cls[cat]) > 5 else ""
                    print("  [{}] {} files: {}{}".format(cat, len(bf_cls[cat]), ", ".join(files[:5]), more))

        model.validate_documents(args.classified)

    model.validate_json(args.extracted, args.classified)
    print("\n" + "#" * 70 + "\n  DONE at " + time.strftime('%Y-%m-%d %H:%M:%S') + "\n" + "#" * 70 + "\n")


if __name__ == "__main__":
    main()
