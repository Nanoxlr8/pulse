#!/usr/bin/env python3
"""PULSE - instant file index + system tuning for this PC.

  pulse index [--root NAME] [--max-seconds N]
  pulse content [--root NAME] [--in FRAG] [--limit N] [--max-seconds N]
  pulse find QUERY [--ext pdf,xlsx] [--root G] [--in FRAG] [--newer 30d] [-n 30] [--json]
  pulse grep QUERY [same filters] [--json]
  pulse stat
  pulse roots [add NAME PATH | rm NAME | content NAME on|off | on NAME | off NAME]
  pulse doctor [--json]
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import db as D
import index as IDX
import search as S
import lock as L

HOME = os.path.expanduser("~")
PULSE_DIR = os.path.join(HOME, ".pulse")
CFG_PATH = os.path.join(PULSE_DIR, "config.json")
DB_PATH = os.path.join(PULSE_DIR, "index.db")

C = {"d": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "c": "\033[36m", "r": "\033[31m", "x": "\033[0m"}
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    C = {k: "" for k in C}


def default_config():
    roots = [{"name": "home", "path": HOME, "content": True, "enabled": True}]
    if os.path.isdir("D:\\"):
        roots.append({"name": "D", "path": "D:\\", "content": True, "enabled": True})
    # Google Drive mounts: index names, content OFF by default so we never
    # force a download of online-only files. Enable per folder with:
    #   pulse content --root G --in "ClientName"
    for letter in ("G", "H", "I"):
        p = letter + ":\\My Drive"
        if os.path.isdir(p):
            roots.append({"name": letter, "path": p,
                          "content": False, "enabled": True})
    return {"db": DB_PATH, "roots": roots}


def load_cfg():
    if not os.path.exists(CFG_PATH):
        cfg = default_config()
        save_cfg(cfg)
        return cfg
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_cfg(cfg):
    os.makedirs(PULSE_DIR, exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def human(n):
    n = float(n or 0)
    for u in ("B", "K", "M", "G", "T"):
        if n < 1024 or u == "T":
            return ("%.0f%s" % (n, u)) if u == "B" else ("%.1f%s" % (n, u))
        n /= 1024.0


def ago(ts):
    if not ts:
        return "?"
    d = time.time() - ts
    if d < 3600:
        return "%.0fm" % (d / 60)
    if d < 86400:
        return "%.0fh" % (d / 3600)
    if d < 2592000:
        return "%.0fd" % (d / 86400)
    return "%.0fmo" % (d / 2592000)


def print_hits(hits, show_snip=False):
    if not hits:
        print(C["y"] + "no match" + C["x"])
        return
    for i, h in enumerate(hits, 1):
        print("%s%3d%s  %s%s%s   %s%s . %s ago . [%s]%s" % (
            C["b"], i, C["x"], C["c"], h["name"], C["x"],
            C["d"], human(h["size"]), ago(h["mtime"]), h["root"], C["x"]))
        print("     " + C["d"] + h["dirpath"] + C["x"])
        if show_snip and h.get("snip"):
            print("     " + C["g"] + h["snip"][:300] + C["x"])


# ---------------------------------------------------------------- commands

def cmd_index(a, cfg, con):
    roots = cfg["roots"]
    if a.root:
        roots = [r for r in roots if r["name"] == a.root]
        if not roots:
            print("no root named " + a.root)
            return 1
    print(C["b"] + "PULSE index" + C["x"])
    t0 = time.time()
    with L.WriteLock(PULSE_DIR, wait=a.wait):
        IDX.build(con, roots, stop_after=a.max_seconds)
        D.set_meta(con, "last_index", int(time.time()))
        con.commit()
    tot = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    print("%sdone%s  %s files indexed  (%.1fs)" % (
        C["g"], C["x"], format(tot, ","), time.time() - t0))
    return 0


def cmd_content(a, cfg, con):
    print(C["b"] + "PULSE content extraction" + C["x"])
    with L.WriteLock(PULSE_DIR, wait=a.wait):
        IDX.content_pass(con, cfg["roots"], limit=a.limit, where_root=a.root,
                         where_in=a.in_, stop_after=a.max_seconds,
                         only_ext=a.ext)
        D.set_meta(con, "last_content", int(time.time()))
        con.commit()
    return 0


def cmd_find(a, cfg, con):
    hits = S.find(con, " ".join(a.query), ext=a.ext, root=a.root,
                  inpath=a.in_, newer=a.newer, bigger=a.bigger, limit=a.n)
    if a.json:
        keys = ("path", "name", "ext", "size", "mtime", "root", "score")
        print(json.dumps([{k: h[k] for k in keys} for h in hits],
                         ensure_ascii=False, indent=1))
    else:
        print_hits(hits)
    return 0


def cmd_grep(a, cfg, con):
    hits = S.grep(con, " ".join(a.query), ext=a.ext, root=a.root,
                  inpath=a.in_, newer=a.newer, bigger=a.bigger, limit=a.n)
    if a.json:
        keys = ("path", "name", "root", "mtime", "score", "snip")
        print(json.dumps([{k: h[k] for k in keys} for h in hits],
                         ensure_ascii=False, indent=1))
    else:
        print_hits(hits, show_snip=True)
    return 0


def cmd_stat(a, cfg, con):
    s = S.stats(con)
    li = D.get_meta(con, "last_index")
    print("%sPULSE index%s   %s files . %s" % (
        C["b"], C["x"], format(s["files"], ","), human(s["bytes"])))
    print("%stext searchable: %s of %s extractable docs (%s not yet read)%s" % (
        C["d"], format(s["content"], ","), format(s["extractable"], ","),
        format(s["pending"], ","), C["x"]))
    dbp = cfg.get("db", DB_PATH)
    if li and os.path.exists(dbp):
        print("%slast index: %s ago . db %s%s" % (
            C["d"], ago(int(li)), human(os.path.getsize(dbp)), C["x"]))
    print()
    print("%s%-8s%12s%12s  PATH%s" % (C["b"], "ROOT", "FILES", "SIZE", C["x"]))
    byname = {r["name"]: r for r in cfg["roots"]}
    for r in s["roots"]:
        cf = byname.get(r["root"], {})
        flag = "content" if cf.get("content") else "names only"
        print("%-8s%12s%12s  %s%s  (%s)%s" % (
            r["root"], format(r["n"], ","), human(r["b"]),
            C["d"], cf.get("path", "?"), flag, C["x"]))
    print()
    print(C["b"] + "top types" + C["x"] + "  " + "  ".join(
        "%s:%s" % (e["ext"], format(e["n"], ",")) for e in s["exts"]))
    return 0


def cmd_roots(a, cfg, con):
    args = a.args
    if not args:
        for r in cfg["roots"]:
            st = (C["g"] + "on " + C["x"]) if r.get("enabled", True) \
                else (C["r"] + "off" + C["x"])
            ct = "content" if r.get("content") else "names  "
            print("  %s %s  %s%-8s%s %s" % (
                st, ct, C["b"], r["name"], C["x"], r["path"]))
        return 0
    op = args[0]
    if op == "add" and len(args) >= 3:
        cfg["roots"].append({"name": args[1], "path": args[2],
                             "content": True, "enabled": True})
    elif op == "rm" and len(args) >= 2:
        cfg["roots"] = [r for r in cfg["roots"] if r["name"] != args[1]]
        IDX.drop_root(con, args[1])
    elif op == "content" and len(args) >= 3:
        for r in cfg["roots"]:
            if r["name"] == args[1]:
                r["content"] = args[2].lower() in ("on", "true", "1", "yes")
    elif op in ("on", "off") and len(args) >= 2:
        for r in cfg["roots"]:
            if r["name"] == args[1]:
                r["enabled"] = (op == "on")
    else:
        print("usage: pulse roots [add NAME PATH | rm NAME | "
              "content NAME on|off | on NAME | off NAME]")
        return 1
    save_cfg(cfg)
    return cmd_roots(argparse.Namespace(args=[]), cfg, con)


def cmd_doctor(a, cfg, con):
    import doctor
    return doctor.run(a, cfg, con, C, human)


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="pulse", description="PULSE - instant file index + PC tuning")
    sub = p.add_subparsers(dest="cmd")

    def filters(sp):
        sp.add_argument("--ext", help="pdf  or  pdf,xlsx,docx")
        sp.add_argument("--root", help="home / D / G / H / I")
        sp.add_argument("--in", dest="in_", action="append",
                        help="path must contain this (repeatable)")
        sp.add_argument("--newer", help="30d 12h 6m 2y")
        sp.add_argument("--bigger", type=int, help="min bytes")
        sp.add_argument("-n", type=int, default=30, help="max results")
        sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("index")
    sp.add_argument("--root")
    sp.add_argument("--max-seconds", type=int, dest="max_seconds")
    sp.add_argument("--wait", type=int, default=0,
                    help="seconds to queue behind another running write")
    sp.set_defaults(fn=cmd_index)

    sp = sub.add_parser("content")
    sp.add_argument("--root")
    sp.add_argument("--in", dest="in_")
    sp.add_argument("--ext", help="restrict to these types, e.g. pdf,xlsx,docx")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--max-seconds", type=int, dest="max_seconds")
    sp.add_argument("--wait", type=int, default=0,
                    help="seconds to queue behind another running write")
    sp.set_defaults(fn=cmd_content)

    sp = sub.add_parser("find")
    sp.add_argument("query", nargs="*")
    filters(sp)
    sp.set_defaults(fn=cmd_find)

    sp = sub.add_parser("grep")
    sp.add_argument("query", nargs="+")
    filters(sp)
    sp.set_defaults(fn=cmd_grep)

    sp = sub.add_parser("stat")
    sp.set_defaults(fn=cmd_stat)

    sp = sub.add_parser("roots")
    sp.add_argument("args", nargs="*")
    sp.set_defaults(fn=cmd_roots)

    sp = sub.add_parser("doctor")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_doctor)

    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0

    cfg = load_cfg()
    con = D.connect(cfg.get("db", DB_PATH))
    try:
        return a.fn(a, cfg, con)
    except L.Busy as e:
        print(C["y"] + "busy: " + str(e) + C["x"])
        return 2
    except KeyboardInterrupt:
        print(C["y"] + "\ninterrupted - progress so far is saved" + C["x"])
        return 130
    finally:
        con.commit()
        con.close()


if __name__ == "__main__":
    sys.exit(main())
