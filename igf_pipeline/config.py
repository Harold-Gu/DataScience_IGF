"""Global configuration: URL templates, session types, year range,
classification rules and noise patterns (Model layer configuration)."""
import re


_RATE_MIN=0.25
_RATE_MAX=10.0

IGF_BASE="https://intgovforum.org"
WORKERS=8
MAX_DEPTH=2;MAX_QUEUE=400
YEAR_START=2006
YEAR_END=2025

_NOISE_RE=re.compile(r"(^|[-_ ])(nav|navbar|navigation|menu|menus|breadcrumb|sidebar|side-bar|footer|header|topbar|toolbar|admin|pager|pagination|search-box|search-form|search-block|language-switcher|skip-link|skip-to-main|site-name|site-slogan|site-header|site-footer|region-header|region-footer|region-sidebar|region-navigation|block-system|block-language|cookie|banner|advert|advertisement|social|share|utility|tabs|login|register|user-menu|contextual)([-_ ]|$)",re.I)

_BIN_MAGIC={".pdf":b"%PDF-",".zip":b"PK",".docx":b"PK",".xlsx":b"PK",".pptx":b"PK",
            ".doc":b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",".xls":b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",".ppt":b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"}

SESSION_TYPES={
    "workshops":["/en/workshop-proposals-{year}","/en/content/igf-{year}-workshops"],
    "open-forums":["/en/open-forum-proposals-{year}","/en/content/igf-{year}-open-forums"],
    "lightning-talks":["/en/lightning-talk-proposals-{year}","/en/content/igf-{year}-lightning-talks"],
    "day-0-events":["/en/pre-events-{year}","/en/content/igf-{year}-day-0-events"],
    "launches-awards":["/en/launches-awards-{year}","/en/content/igf-{year}-launches-awards"],
    "networking":["/en/networking-sessions-{year}","/en/content/igf-{year}-networking-sessions"],
    "main-sessions":["/en/content/igf-{year}-main-sessions"],
    "town-halls":["/en/content/igf-{year}-town-halls"],
}

DETAIL_RE=re.compile(r"igf-\d{4}-(?:ws|workshop|open-forum|lightning-talk|lightning-talk-event|day-0-event|networking-session|networking|launch-award-event|town-hall|main-session|pre-event)-\d+",re.I)

_REPORT_HINTS=["report","outcome","chair's summary","chair summary","chairs summary","chairman's summary",
               "session report","final report","annual report","synthesis","messages","proceedings",
               "executive summary","rapporteur","key findings","recommendations"]

ARCHIVED={
    2006:"/en/archived/first-igf-meeting-athens-greece",
    2007:"/en/archived/second-igf-meeting-rio-de-janeiro-brazil",
    2008:"/en/archived/the-igf-2008-meeting",
    2009:"/en/archived/the-igf-2009-meeting",
    2010:"/en/archived/the-igf-2010-meeting",
    2011:"/en/archived/igf-2011",
    2012:"/en/archived/igf-2012",
    2013:"/en/archived/igf-2013",
    2014:"/en/archived/igf-2014",
    2015:"/en/archived/igf-2015",
    2016:"/en/archived/igf-2016-enabling-inclusive-and-sustainable-growth",
    2017:"/en/archived/igf-2017",
    2018:"/en/archived/igf-2018",
    2019:"/en/archived/igf-2019",
    2020:"/en/archived/igf-2020",
    2021:"/en/archived/igf-2021",
}

DASHBOARD={
    2022:"/en/dashboard/igf-2022",
    2023:"/en/dashboard/igf-2023",
    2024:"/en/dashboard/igf-2024",
    2025:"/en/dashboard/igf-2025",
}

PARTICIPANTS={
    2021:"https://indico.un.org/event/36215/registrations/participants",
    2022:"https://indico.un.org/event/1002089/registrations/participants",
    2023:"https://indico.un.org/event/1006568/registrations/participants",
    2025:"https://indico.un.org/event/1016806/registrations/participants",
}

TYPE_PATTERNS={
    "workshop":[r"igf-\d{4}-ws-\d+",r"igf-\d{4}-workshop-"],
    "open-forum":[r"igf-\d{4}-open-forum-",r"igf-\d{4}-of-\d+"],
    "lightning-talk":[r"igf-\d{4}-lightning-talk"],
    "day-0-event":[r"igf-\d{4}-day-0-event",r"igf-\d{4}-pre-event"],
    "launch-award":[r"igf-\d{4}-launch-award",r"igf-\d{4}-launches-awards"],
    "networking":[r"igf-\d{4}-networking"],
    "main-session":[r"igf-\d{4}-main-session",r"high-level-track",r"opening-ceremony",r"closing-ceremony",r"parliamentary-track",r"open-mic"],
    "town-hall":[r"igf-\d{4}-town-hall"],
    "report":[r"igf-\d{4}-report",r"session-report",r"report",r"final-report"],
    "transcript":[r"transcript",r"igf-\d{4}-transcript"],
    "schedule":[r"igf-\d{4}-schedule",r"schedule"],
}

TYPE_RE={k:[re.compile(p,re.I)for p in v]for k,v in TYPE_PATTERNS.items()}

WEIGHTED_RULES=[
    (["workshop","ws #","breakout","ws-"],"workshop",5),
    (["open forum","of #","open-forum"],"open-forum",5),
    (["lightning talk","lightning-talk","lightning talk event"],"lightning-talk",5),
    (["day 0","day-0","pre-event","pre event","day 0 event"],"day-0-event",5),
    (["launch","award","laureate","launches & awards"],"launch-award",4),
    (["networking","networking session"],"networking",5),
    (["main session","plenary","high-level session","high level session",
      "opening session","closing session","opening ceremony","closing ceremony","open mic"],"main-session",5),
    (["town hall","townhall"],"town-hall",5),
    (["transcript","verbatim","proceedings","record of","meeting record"],"transcript",4),
    (["executive summary","session report","outcome document","final report",
      "meeting report","summary report","annual report","chair summary","rapporteur"],"report",3),
    (["schedule","agenda","programme","program overview","timetable","calendar"],"schedule",3),
    (["participant","registration list","attendee"],"participants",4),
    (["dynamic coalition","dc session","bpf","best practice","nri",
      "national regional","intersessional"],"dc-bpf-nri",4),
]

TYPE_PRIORITY={"workshop":0,"open-forum":1,"lightning-talk":2,"day-0-event":3,
    "networking":4,"main-session":5,"town-hall":6,"launch-award":7,
    "transcript":8,"report":9,"schedule":10,"participants":11,"dc-bpf-nri":12}

STEPS=["sessions","reports","transcripts","schedules","archived","dashboard","participants"]

def year_range(years=None):
    """Resolve the years= debug filter to a concrete list of years.

    None  -> the full configured range (identical to the original behaviour)
    int   -> a single year
    str   -> '2020', or '2020-2022'
    list  -> the given years, coerced to int
    """
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

