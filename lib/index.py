"""Index build / update for PULSE."""
import os, re, sys, time
import db as D
import walk as W
import extract as X

CHUNK = 900


def _chunks(seq, n=CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def upsert_batch(con, batch, gen):
    """batch rows: (path,name,ext,dirpath,size,mtime,root)"""
    bypath = {}
    for r in batch:
        bypath[r[0]] = r
    paths = list(bypath)

    existing = {}
    for ch in _chunks(paths):
        q = ",".join("?" * len(ch))
        for r in con.execute(
                f"SELECT id,path,mtime,size FROM files WHERE path IN ({q})", ch):
            existing[r["path"]] = (r["id"], r["mtime"], r["size"])

    new_rows, upd = [], []
    for p, r in bypath.items():
        _, name, ext, dirpath, size, mt, root = r
        if p in existing:
            fid, _om, _os = existing[p]
            upd.append({"sz": size, "mt": mt, "gen": gen, "id": fid})
        else:
            new_rows.append((p, name, name.lower(), ext, dirpath,
                             size, mt, root, gen))

    if upd:
        con.executemany(
            "UPDATE files SET "
            " c_state = CASE WHEN mtime <> :mt OR size <> :sz THEN 0 ELSE c_state END,"
            " size = :sz, mtime = :mt, gen = :gen "
            "WHERE id = :id", upd)

    if new_rows:
        con.executemany(
            "INSERT OR IGNORE INTO files"
            "(path,name,namelow,ext,dirpath,size,mtime,root,gen) "
            "VALUES(?,?,?,?,?,?,?,?,?)", new_rows)
        newpaths = [r[0] for r in new_rows]
        idmap = {}
        for ch in _chunks(newpaths):
            q = ",".join("?" * len(ch))
            for r in con.execute(
                    f"SELECT id,path,name,dirpath FROM files WHERE path IN ({q})", ch):
                idmap[r["path"]] = (r["id"], r["name"], r["dirpath"])
        fts = []
        for p in newpaths:
            if p not in idmap:
                continue
            fid, name, dirpath = idmap[p]
            fts.append((fid, D.tokenize(name), D.tokenize(dirpath)))
        if fts:
            # Clear any stale fts row for these ids first: fts5 has no
            # upsert, and a leftover rowid would fail the insert.
            ids = [f[0] for f in fts]
            for ch in _chunks(ids):
                q = ",".join("?" * len(ch))
                con.execute(f"DELETE FROM name_fts WHERE rowid IN ({q})", ch)
            con.executemany(
                "INSERT INTO name_fts(rowid,toks,dirtoks) VALUES(?,?,?)", fts)

    return len(new_rows), len(upd)


def reap(con, root, gen):
    """Remove rows for files that vanished from this root."""
    ids = [r[0] for r in con.execute(
        "SELECT id FROM files WHERE root=? AND gen<>?", (root, gen))]
    for ch in _chunks(ids):
        q = ",".join("?" * len(ch))
        con.execute(f"DELETE FROM name_fts WHERE rowid IN ({q})", ch)
        con.execute(f"DELETE FROM content_fts WHERE rowid IN ({q})", ch)
        con.execute(f"DELETE FROM files WHERE id IN ({q})", ch)
    return len(ids)


def build(con, roots, verbose=True, stop_after=None):
    total_new = total_upd = total_del = 0
    for root in roots:
        if not root.get("enabled", True):
            continue
        path, name = root["path"], root["name"]
        if not os.path.isdir(path):
            if verbose:
                print(f"  [{name}] skip - not mounted: {path}")
            continue
        gen = int(time.time() * 1000) & 0x7FFFFFFF
        state = {"new": 0, "upd": 0}

        def on_batch(b, _gen=gen, _st=state):
            n, u = upsert_batch(con, b, _gen)
            _st["new"] += n
            _st["upd"] += u

        def prog(nf, nd, el):
            if verbose:
                sys.stdout.write(f"\r  [{name}] {nf:,} files / {nd:,} dirs  {el:.0f}s ")
                sys.stdout.flush()

        t0 = time.time()
        nf, nd, el, trunc = W.crawl(path, name, on_batch,
                                    stop_after=stop_after, progress=prog)
        deleted = 0 if trunc else reap(con, name, gen)
        con.commit()
        total_new += state["new"]; total_upd += state["upd"]; total_del += deleted
        if verbose:
            flag = "  (TIME-LIMITED)" if trunc else ""
            sys.stdout.write("\r" + " " * 70 + "\r")
            print(f"  [{name}] {nf:,} files  {el:.1f}s  "
                  f"+{state['new']:,} new  ~{state['upd']:,} seen  "
                  f"-{deleted:,} gone{flag}")
    return total_new, total_upd, total_del


def content_pass(con, roots, limit=None, where_root=None, where_in=None,
                 verbose=True, stop_after=None, only_ext=None):
    """Extract text for files whose root has content=True and c_state=0.

    only_ext: comma/space list like "pdf,xlsx,docx" to restrict the pass.
    where_root may name a root whose content flag is off - naming it
    explicitly is treated as consent to read that root."""
    allowed = [r["name"] for r in roots
               if r.get("content") and r.get("enabled", True)]
    if where_root:
        allowed = [where_root]
    if not allowed:
        print("  no content-enabled roots selected "
              "(enable with: pulse roots content <name> on)")
        return 0, 0

    wanted = set(X.SUPPORTED)
    if only_ext:
        ask = {e if e.startswith(".") else "." + e
               for e in re.split(r"[,\s]+", only_ext.lower()) if e}
        wanted = wanted & ask
        if not wanted:
            print("  none of those extensions can be text-extracted; "
                  "supported: " + " ".join(sorted(X.SUPPORTED)))
            return 0, 0

    q = ",".join("?" * len(allowed))
    exts = ",".join("?" * len(wanted))
    sql = (f"SELECT id,path,ext,size FROM files "
           f"WHERE c_state=0 AND root IN ({q}) AND ext IN ({exts})")
    args = list(allowed) + sorted(wanted)
    if where_in:
        sql += " AND path LIKE ?"
        args.append(f"%{where_in}%")
    sql += " ORDER BY mtime DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"

    rows = con.execute(sql, args).fetchall()
    done = fail = 0
    t0 = time.time()
    for i, r in enumerate(rows):
        if stop_after and time.time() - t0 > stop_after:
            if verbose:
                print(f"\n  stopped at time limit ({stop_after}s)")
            break
        txt, state = X.extract(r["path"], r["ext"], r["size"])
        con.execute("DELETE FROM content_fts WHERE rowid=?", (r["id"],))
        if state == 1:
            con.execute("INSERT INTO content_fts(rowid,body) VALUES(?,?)",
                        (r["id"], txt))
            done += 1
        else:
            fail += 1
        con.execute("UPDATE files SET c_state=?, c_mtime=? WHERE id=?",
                    (state, time.time(), r["id"]))
        if i % 200 == 0:
            con.commit()
            if verbose:
                sys.stdout.write(f"\r  content {i+1:,}/{len(rows):,}  "
                                 f"ok={done:,} skip={fail:,}  {time.time()-t0:.0f}s ")
                sys.stdout.flush()
    con.commit()
    if verbose:
        sys.stdout.write("\r" + " " * 70 + "\r")
        print(f"  content: {done:,} indexed, {fail:,} skipped/failed, "
              f"{time.time()-t0:.1f}s")
    return done, fail


def drop_root(con, root):
    """Remove a root and every trace of it from all three tables."""
    ids = [r[0] for r in con.execute(
        "SELECT id FROM files WHERE root=?", (root,))]
    for ch in _chunks(ids):
        q = ",".join("?" * len(ch))
        con.execute(f"DELETE FROM name_fts WHERE rowid IN ({q})", ch)
        con.execute(f"DELETE FROM content_fts WHERE rowid IN ({q})", ch)
        con.execute(f"DELETE FROM files WHERE id IN ({q})", ch)
    con.commit()
    return len(ids)
