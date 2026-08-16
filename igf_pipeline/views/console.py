"""Console helpers used by the CLI."""


def section(title, width=55):
    print("\n" + "=" * width)
    print("  " + title)
    print("=" * width)


def note(text):
    print("  [NOTE] " + text)

