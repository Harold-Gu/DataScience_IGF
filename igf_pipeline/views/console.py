"""Console rendering helpers (View layer) used by the CLI entry point.
The pipeline's progress prints live in the model/controller modules to keep
behaviour byte-identical with the original single-file scraper."""


def section(title, width=55):
    print("\n" + "=" * width)
    print("  " + title)
    print("=" * width)


def note(text):
    print("  [NOTE] " + text)

