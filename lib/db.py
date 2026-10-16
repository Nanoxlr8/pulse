"""PULSE index store: SQLite + FTS5 (Arabic-aware)."""
import os, re, sqlite3, time

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS files(
  id            INTEGER PRIMARY KEY,
  path          TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  namelow       TEXT NOT NULL,
  ext           TEXT,
  dirpath       TEXT,
  size          INTEGER,
  mtime         REAL,
  root          TEXT,
  gen           INTEGER DEFAULT 0,
  c_state       INTEGER DEFAULT 0,   -- 0 todo 1 done 2 skip 3 error
  c_mtime       REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_files_root    ON files(root);
CREATE INDEX IF NOT EXISTS ix_files_ext     ON files(ext);
CREATE INDEX IF NOT EXISTS ix_files_mtime   ON files(mtime);
CREATE INDEX IF NOT EXISTS ix_files_cstate  ON files(c_state);
CREATE INDEX IF NOT EXISTS ix_files_namelow ON files(namelow);

CREATE VIRTUAL TABLE IF NOT EXISTS name_fts USING fts5(
  toks, dirtoks,
  tokenize="unicode61 remove_diacritics 2"
);

CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
  body,
  tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

# Letters we keep as word characters: latin, digits, Arabic block + supplement
_SPLIT = re.compile(r"[^0-9A-Za-z؀-ۿݐ-ݿ]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NUMSEP = re.compile(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def tokenize(text: str) -> str:
    """Filename -> whitespace token string, split on punctuation, camelCase
    and letter/digit boundaries so 'BankStmt2026-09.pdf' matches 'stmt' and '2026'."""
    if not text:
        return ""
    out = []
    for piece in _SPLIT.split(text):
        if not piece:
            continue
        out.append(piece)
        for sub in _CAMEL.split(piece):
            for sub2 in _NUMSEP.split(sub):
                if sub2 and sub2 != piece:
                    out.append(sub2)
    # dedupe, preserve order
    seen, res = set(), []
    for t in out:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            res.append(tl)
    return " ".join(res)


def connect(dbpath: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(dbpath), exist_ok=True)
    con = sqlite3.connect(dbpath, timeout=120)
    con.row_factory = sqlite3.Row
    # Belt and braces alongside the timeout above: a reader that arrives
    # mid-commit should wait, not raise "database is locked".
    con.execute("PRAGMA busy_timeout=120000")
    con.executescript(SCHEMA)
    return con


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_meta(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def fts_escape(q: str) -> str:
    """Turn a user phrase into a safe FTS5 MATCH expression (AND of prefix terms)."""
    terms = [t for t in _SPLIT.split(q) if t]
    if not terms:
        return ""
    return " AND ".join('"%s"*' % t.replace('"', '""') for t in terms)
