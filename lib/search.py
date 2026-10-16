"""Query layer for PULSE."""
import os, re, time
import db as D

_SPLIT = D._SPLIT


def _age_filter(newer):
    """'30d' '12h' '6m' '2y' -> epoch cutoff"""
    if not newer:
        return None
    m = re.fullmatch(r"(\d+)\s*([hdwmy])", newer.strip().lower())
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    mult = {"h": 3600, "d": 86400, "w": 604800,
            "m": 2592000, "y": 31536000}[u]
    return time.time() - n * mult


def _filters(ext, root, inpath, newer, bigger):
    cl, args = [], []
    if ext:
        exts = [e if e.startswith(".") else "." + e
                for e in re.split(r"[,\s]+", ext.lower()) if e]
        cl.append("f.ext IN (%s)" % ",".join("?" * len(exts)))
        args += exts
    if root:
        roots = [r for r in re.split(r"[,\s]+", root) if r]
        cl.append("f.root IN (%s)" % ",".join("?" * len(roots)))
        args += roots
    if inpath:
        for frag in inpath if isinstance(inpath, list) else [inpath]:
            cl.append("f.path LIKE ?")
            args.append(f"%{frag}%")
    cut = _age_filter(newer)
    if cut:
        cl.append("f.mtime > ?")
        args.append(cut)
    if bigger:
        cl.append("f.size > ?")
        args.append(int(bigger))
    return cl, args


def find(con, query, ext=None, root=None, inpath=None, newer=None,
         bigger=None, limit=30):
    q = (query or "").strip()
    terms = [t.lower() for t in _SPLIT.split(q) if t]
    cl, args = _filters(ext, root, inpath, newer, bigger)

    rows = {}
    if terms:
        match = D.fts_escape(q)
        where = ["name_fts MATCH ?"] + cl
        sql = ("SELECT f.id,f.path,f.name,f.namelow,f.ext,f.dirpath,f.size,"
               "f.mtime,f.root, bm25(name_fts,8.0,1.0) AS rk "
               "FROM name_fts JOIN files f ON f.id = name_fts.rowid "
               "WHERE " + " AND ".join(where) +
               " ORDER BY rk LIMIT ?")
        for r in con.execute(sql, [match] + args + [limit * 12]):
            rows[r["id"]] = dict(r)

        # substring fallback for terms FTS can't tokenize into a prefix hit
        if len(rows) < limit:
            where2 = ["f.namelow LIKE ?"] + cl
            sql2 = ("SELECT f.id,f.path,f.name,f.namelow,f.ext,f.dirpath,"
                    "f.size,f.mtime,f.root, 0.0 AS rk FROM files f WHERE "
                    + " AND ".join(where2) + " ORDER BY f.mtime DESC LIMIT ?")
            like = "%" + "%".join(terms) + "%"
            for r in con.execute(sql2, [like] + args + [limit * 6]):
                rows.setdefault(r["id"], dict(r))
    else:
        where = cl or ["1=1"]
        sql = ("SELECT f.id,f.path,f.name,f.namelow,f.ext,f.dirpath,f.size,"
               "f.mtime,f.root, 0.0 AS rk FROM files f WHERE "
               + " AND ".join(where) + " ORDER BY f.mtime DESC LIMIT ?")
        for r in con.execute(sql, args + [limit]):
            rows[r["id"]] = dict(r)

    now = time.time()
    out = []
    for r in rows.values():
        nl = r["namelow"]
        stem = os.path.splitext(nl)[0]
        s = -(r["rk"] or 0.0) * 1.0
        if terms:
            joined = " ".join(terms)
            if stem == joined or nl == joined:
                s += 120
            elif stem.startswith(joined) or nl.startswith(joined):
                s += 60
            hits = sum(1 for t in terms if t in nl)
            s += 14 * hits
            if hits == len(terms):
                s += 25
            dl = r["dirpath"].lower()
            s += 3 * sum(1 for t in terms if t in dl)
        age_days = max((now - (r["mtime"] or 0)) / 86400.0, 0.0)
        s += 12.0 / (1.0 + age_days / 45.0)
        r["score"] = round(s, 2)
        out.append(r)

    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def grep(con, query, ext=None, root=None, inpath=None, newer=None,
         bigger=None, limit=25, snippets=True):
    q = (query or "").strip()
    if not q:
        return []
    match = D.fts_escape(q)
    cl, args = _filters(ext, root, inpath, newer, bigger)
    where = ["content_fts MATCH ?"] + cl
    snip = ("snippet(content_fts, 0, '>>', '<<', ' ... ', 18)"
            if snippets else "''")
    sql = (f"SELECT f.id,f.path,f.name,f.ext,f.dirpath,f.size,f.mtime,f.root,"
           f" bm25(content_fts) AS rk, {snip} AS snip "
           f"FROM content_fts JOIN files f ON f.id = content_fts.rowid "
           f"WHERE " + " AND ".join(where) + " ORDER BY rk LIMIT ?")
    out = []
    for r in con.execute(sql, [match] + args + [limit]):
        d = dict(r)
        d["score"] = round(-(d["rk"] or 0), 2)
        d["snip"] = re.sub(r"\s+", " ", d["snip"] or "").strip()
        out.append(d)
    return out


def stats(con):
    s = {}
    s["files"] = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    s["bytes"] = con.execute(
        "SELECT COALESCE(SUM(size),0) c FROM files").fetchone()["c"]
    s["content"] = con.execute(
        "SELECT COUNT(*) c FROM files WHERE c_state=1").fetchone()["c"]
    # Pending means "extractable but not yet read" - counting every png and
    # .go file as pending would be a meaningless number.
    import extract as X
    sup = sorted(X.SUPPORTED)
    q = ",".join("?" * len(sup))
    s["pending"] = con.execute(
        f"SELECT COUNT(*) c FROM files WHERE c_state=0 AND ext IN ({q})",
        sup).fetchone()["c"]
    s["extractable"] = con.execute(
        f"SELECT COUNT(*) c FROM files WHERE ext IN ({q})", sup).fetchone()["c"]
    s["roots"] = [dict(r) for r in con.execute(
        "SELECT root, COUNT(*) n, COALESCE(SUM(size),0) b, MAX(mtime) newest "
        "FROM files GROUP BY root ORDER BY n DESC")]
    s["exts"] = [dict(r) for r in con.execute(
        "SELECT ext, COUNT(*) n FROM files WHERE ext<>'' "
        "GROUP BY ext ORDER BY n DESC LIMIT 15")]
    return s
