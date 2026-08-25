import re
import threading


_RATE_MIN = 0.25
_RATE_MAX = 10.0

IGF_BASE = "https://intgovforum.org"
WORKERS = 8
MAX_DEPTH = 2
MAX_QUEUE = 400
YEAR_START = 2006
YEAR_END = 2025

_NOISE_RE = re.compile(r"(^|[-_ ])(nav|navbar|navigation|menu|menus|breadcrumb|sidebar|side-bar|footer|header|topbar|toolbar|admin|pager|pagination|search-box|search-form|search-block|language-switcher|skip-link|skip-to-main|site-name|site-slogan|site-header|site-footer|region-header|region-footer|region-sidebar|region-navigation|block-system|block-language|cookie|banner|advert|advertisement|social|share|utility|tabs|login|register|user-menu|contextual)([-_ ]|$)", re.I)

_BIN_MAGIC = {".pdf": b"%PDF-", ".zip": b"PK", ".docx": b"PK", ".xlsx": b"PK", ".pptx": b"PK",
              ".doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".ppt": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"}

SESSION_TYPES = {
    "workshops": ["/en/workshop-proposals-{year}", "/en/content/igf-{year}-workshops"],
    "open-forums": ["/en/open-forum-proposals-{year}", "/en/content/igf-{year}-open-forums"],
    "lightning-talks": ["/en/lightning-talk-proposals-{year}", "/en/content/igf-{year}-lightning-talks"],
    "day-0-events": ["/en/pre-events-{year}", "/en/content/igf-{year}-day-0-events"],
    "launches-awards": ["/en/launches-awards-{year}", "/en/content/igf-{year}-launches-awards"],
    "networking": ["/en/networking-sessions-{year}", "/en/content/igf-{year}-networking-sessions"],
    "main-sessions": ["/en/content/igf-{year}-main-sessions"],
    "town-halls": ["/en/content/igf-{year}-town-halls"],
}

DETAIL_RE = re.compile(r"igf-\d{4}-(?:ws|workshop|open-forum|lightning-talk|lightning-talk-event|day-0-event|networking-session|networking|launch-award-event|town-hall|main-session|pre-event)-\d+", re.I)

_REPORT_HINTS = ["report", "outcome", "chair's summary", "chair summary", "chairs summary", "chairman's summary",
                 "session report", "final report", "annual report", "synthesis", "messages", "proceedings",
                 "executive summary", "rapporteur", "key findings", "recommendations"]

ARCHIVED = {
    2006: "/en/archived/first-igf-meeting-athens-greece",
    2007: "/en/archived/second-igf-meeting-rio-de-janeiro-brazil",
    2008: "/en/archived/the-igf-2008-meeting",
    2009: "/en/archived/the-igf-2009-meeting",
    2010: "/en/archived/the-igf-2010-meeting",
    2011: "/en/archived/igf-2011",
    2012: "/en/archived/igf-2012",
    2013: "/en/archived/igf-2013",
    2014: "/en/archived/igf-2014",
    2015: "/en/archived/igf-2015",
    2016: "/en/archived/igf-2016-enabling-inclusive-and-sustainable-growth",
    2017: "/en/archived/igf-2017",
    2018: "/en/archived/igf-2018",
    2019: "/en/archived/igf-2019",
    2020: "/en/archived/igf-2020",
    2021: "/en/archived/igf-2021",
}

DASHBOARD = {
    2022: "/en/dashboard/igf-2022",
    2023: "/en/dashboard/igf-2023",
    2024: "/en/dashboard/igf-2024",
    2025: "/en/dashboard/igf-2025",
}

PARTICIPANTS = {
    2021: "https://indico.un.org/event/36215/registrations/participants",
    2022: "https://indico.un.org/event/1002089/registrations/participants",
    2023: "https://indico.un.org/event/1006568/registrations/participants",
    2025: "https://indico.un.org/event/1016806/registrations/participants",
}

# Filename classification runs in two passes. Pass 1 contains session-id and
# unambiguous tokens, ordered so an explicit session id wins over a topic word
# (e.g. "town-hall-32-...-open-forum" is a town hall, "of-23-...-awards" is an
# open forum, "ws407-book-launch-..." is a workshop). Pass 2 contains generic
# words and is consulted only when pass 1 produced no match.
TYPE_P1 = [
    ("transcript", [r"transcript", r"verbatim"]),
    ("schedule", [r"workshopschedule", r"schedule"]),
    ("open-forum", [r"open-forum[-_#\s]{0,2}\d+",
                    r"(?:^|[^a-z0-9])of-?(?!\d{4})0*[1-9]\d{0,2}(?=[^0-9]|$)"]),
    ("workshop", [r"igf-\d{4}-ws-?\d+", r"(?:^|[^a-z])ws-?\d+",
                  r"igf-\d{4}-workshops?", r"workshop[-_\s]?\d+"]),
    ("lightning-talk", [r"lightning"]),
    ("launch-award", [r"launch-award[-_\s]?event[-_\s]?\d+",
                      r"launch-award[-_\s]?\d+", r"launches-awards"]),
    ("day-0-event", [r"day-0[-_\s\u2013]{0,4}event[-_\s#]?\d+",
                     r"pre-event[-_\s]?\d+"]),
    ("town-hall", [r"town-?hall[-_\s]?\d+", r"town-?hall"]),
    ("networking", [r"networking[-_\s]?(?:session)?[-_\s]?\d+", r"networking"]),
    ("main-session", [r"main-session", r"plenary", r"opening-ceremony",
                      r"closing-ceremony", r"open-mic", r"parliamentary"]),
    ("workshop", [r"workshop-room"]),
    ("launch-award", [r"launch", r"award", r"laureate"]),
    ("dc-bpf-nri", [r"dynamic[_-]coalition", r"(?:^|[^a-z])dc-[a-z]",
                    r"dccos", r"bpf", r"(?:^|[^a-z])nri",
                    r"intersessional", r"best-practice"]),
    ("participants", [r"participant", r"registration", r"attendee"]),
    ("report", [r"igf-\d{4}-report", r"-report", r"report"]),
]

TYPE_P2 = [
    ("workshop", [r"workshop"]),
    ("open-forum", [r"open-forum"]),
    ("day-0-event", [r"day-0", r"pre-event"]),
    ("lightning-talk", [r"lightning"]),
    ("town-hall", [r"town-?hall"]),
    ("networking", [r"networking"]),
    ("main-session", [r"main-session", r"plenary", r"high-level"]),
    ("launch-award", [r"launch", r"award", r"laureate"]),
    ("dc-bpf-nri", [r"dynamic[_-]coalition", r"bpf", r"(?:^|[^a-z])nri",
                    r"intersessional", r"best-practice"]),
    ("participants", [r"participant", r"registration", r"attendee"]),
    ("report", [r"report"]),
    ("schedule", [r"schedule", r"agenda", r"timetable", r"programme", r"calendar"]),
]

TYPE_RE_P1 = [(t, [re.compile(p, re.I) for p in ps]) for t, ps in TYPE_P1]
TYPE_RE_P2 = [(t, [re.compile(p, re.I) for p in ps]) for t, ps in TYPE_P2]

WEIGHTED_RULES = [
    (["workshop", "ws #", "breakout", "ws-"], "workshop", 5),
    (["open forum", "of #", "open-forum"], "open-forum", 5),
    (["lightning talk", "lightning-talk", "lightning talk event"], "lightning-talk", 5),
    (["day 0", "day-0", "pre-event", "pre event", "day 0 event"], "day-0-event", 5),
    (["launch", "award", "laureate", "launches & awards"], "launch-award", 4),
    (["networking", "networking session"], "networking", 5),
    (["main session", "plenary", "high-level session", "high level session",
      "opening session", "closing session", "opening ceremony", "closing ceremony", "open mic"], "main-session", 5),
    (["town hall", "townhall"], "town-hall", 5),
    (["transcript", "verbatim", "proceedings", "record of", "meeting record"], "transcript", 4),
    (["executive summary", "session report", "outcome document", "final report",
      "meeting report", "summary report", "annual report", "chair summary", "rapporteur"], "report", 3),
    (["schedule", "agenda", "programme", "program overview", "timetable", "calendar"], "schedule", 3),
    (["participant", "registration list", "attendee"], "participants", 4),
    (["dynamic coalition", "dc session", "bpf", "best practice", "nri",
      "national regional", "intersessional"], "dc-bpf-nri", 4),
]

TYPE_PRIORITY = {"workshop": 0, "open-forum": 1, "lightning-talk": 2, "day-0-event": 3,
                 "networking": 4, "main-session": 5, "town-hall": 6, "launch-award": 7,
                 "transcript": 8, "report": 9, "schedule": 10, "participants": 11, "dc-bpf-nri": 12}

STEPS = ["sessions", "reports", "transcripts", "schedules", "archived", "dashboard", "participants"]

SEP = "=" * 60
SEP2 = "-" * 60


def year_range(years=None):
    """None -> full range, int -> one year, '2020' or '2020-2022' -> filtered list."""
    if years is None:
        return list(range(YEAR_START, YEAR_END + 1))
    if isinstance(years, int):
        return [years]
    if isinstance(years, str):
        years = years.strip()
        m = re.match(r"^(\d{4})\s*-\s*(\d{4})$", years)
        if m:
            return list(range(int(m.group(1)), int(m.group(2)) + 1))
        m2 = re.match(r"^(\d{4})$", years)
        if m2:
            return [int(m2.group(1))]
        return list(range(YEAR_START, YEAR_END + 1))
    return [int(y) for y in years]


_visited_lock = threading.Lock()
_visited_urls = set()
_inflight_urls = set()
_stats_lock = threading.Lock()
_stats = {"ok": 0, "fail": 0, "skip": 0, "pages": 0, "errors": 0}

_rate_lock = threading.Lock()
_rate_state = {"gap": 0.35, "next_ts": 0.0, "streak": 0, "cooldown_until": 0.0}

_failed_lock = threading.Lock()
_failed_seen = set()
_failed_log_path = [None]

_classify_errors = []
_classify_err_lock = threading.Lock()

_MANIFEST = {}

_GLOBAL_SCRAPER = None
_GLOBAL_SCRAPER_LOCK = threading.Lock()

_fetch_err = [None, 0]

_wb_lock = threading.Lock()
_wb_state = {"fails": 0, "disabled": False}

_FILE_MAP = []
_FILE_MAP_LOCK = threading.Lock()
