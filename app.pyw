#!/usr/bin/env python3
"""PULSE - desktop app. Search everything on this PC, and keep it fast.

Launched by the "PULSE" desktop shortcut. No commands needed.
"""
import os
import sys
import time
import queue
import socket
import threading
import subprocess

import tkinter as tk
from tkinter import ttk, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import db as D
import search as S

HOME = os.path.expanduser("~")
PULSE_DIR = os.path.join(HOME, ".pulse")
DB_PATH = os.path.join(PULSE_DIR, "index.db")

# ------------------------------------------------------------------ theme

BG = "#16181d"
BG2 = "#1e2128"
BG3 = "#272b34"
FG = "#e6e8ec"
FG_DIM = "#8b93a1"
ACCENT = "#4c9aff"
GOOD = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
FONT_BIG = ("Segoe UI", 13)
FONT_H = ("Segoe UI Semibold", 15)
FONT_MONO = ("Consolas", 9)


# Single instance. The desktop shortcut carries a global hotkey, so pressing
# it again must raise the window that is already open instead of starting a
# second copy. A bound localhost port is the handshake.
#
# The port is NOT hardcoded: Windows reserves large dynamic ranges for Hyper-V
# (netsh interface ipv4 show excludedportrange) and binding inside one fails
# with WinError 10013. Those ranges move across reboots, so the first instance
# binds whichever candidate works and records it for the next one to find.
PORT_FILE = os.path.join(PULSE_DIR, "app.port")
CANDIDATE_PORTS = (47811, 47812, 47813, 45237, 45238, 38711, 38712, 33501)


def already_running():
    """If another PULSE is up, tell it to show itself and return True."""
    try:
        with open(PORT_FILE, encoding="utf-8") as f:
            port = int(f.read().strip())
    except (OSError, ValueError):
        return False
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=0.8)
        c.sendall(b"show")
        c.close()
        return True
    except OSError:
        return False        # stale file - that instance is gone


def bind_singleton():
    """Bind the first usable candidate port and record it. None if all fail."""
    for port in CANDIDATE_PORTS:
        try:
            srv = socket.socket()
            srv.bind(("127.0.0.1", port))
            srv.listen(4)
        except OSError:
            continue
        try:
            os.makedirs(PULSE_DIR, exist_ok=True)
            with open(PORT_FILE, "w", encoding="utf-8") as f:
                f.write(str(port))
        except OSError:
            pass
        return srv
    return None


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.0f %s" % (n, u) if u == "B" else "%.1f %s" % (n, u)
        n /= 1024.0


def ago(ts):
    if not ts:
        return ""
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return "%.0f min ago" % (d / 60)
    if d < 86400:
        return "%.0f hr ago" % (d / 3600)
    if d < 2592000:
        return "%.0f days ago" % (d / 86400)
    if d < 31536000:
        return "%.0f months ago" % (d / 2592000)
    return "%.0f years ago" % (d / 31536000)


# ------------------------------------------------------------------ app

class Pulse(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PULSE")
        w, h = 1180, 740
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                       max(0, (sh - h) // 2 - 30)))
        self.minsize(900, 560)
        self.configure(bg=BG)
        try:
            self.iconbitmap(default="")
        except Exception:
            pass

        self._style()
        self.q = queue.Queue()
        self.seq = 0            # search generation, so stale results are dropped
        self.after_id = None
        self.rows = []

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=0, pady=0)
        self.tab_search = tk.Frame(nb, bg=BG)
        self.tab_health = tk.Frame(nb, bg=BG)
        nb.add(self.tab_search, text="   Search   ")
        nb.add(self.tab_health, text="   PC Health   ")
        self.nb = nb
        nb.bind("<<NotebookTabChanged>>", self._tab_changed)

        self._build_search(self.tab_search)
        self._build_health(self.tab_health)

        self.bind("<Escape>", lambda e: self.entry.focus_set())
        self.bind("<Control-f>", lambda e: self.entry.focus_set())
        self.after(80, self._pump)
        self.entry.focus_set()
        self._load_stats()
        self._serve_singleton()

    # ---------------------------------------------------------- singleton

    def _serve_singleton(self):
        srv = bind_singleton()
        if srv is None:
            return          # no usable port; app still works, hotkey may
                            # open a second window instead of raising this one

        def loop():
            while True:
                try:
                    conn, _ = srv.accept()
                    conn.recv(16)
                    conn.close()
                    self.q.put(("show", 0, None, None))
                except OSError:
                    break

        threading.Thread(target=loop, daemon=True).start()

    def surface(self):
        """Bring the window to the front and focus the search box."""
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.attributes("-topmost", True)
            self.after(220, lambda: self.attributes("-topmost", False))
            self.focus_force()
            self.entry.focus_set()
            self.entry.select_range(0, "end")
        except Exception:
            pass

    # -------------------------------------------------------------- style

    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=FG_DIM,
                    padding=(18, 9), font=FONT_B, borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", BG)],
              foreground=[("selected", FG)])
        s.configure("Treeview", background=BG2, fieldbackground=BG2,
                    foreground=FG, borderwidth=0, rowheight=46, font=FONT)
        s.configure("Treeview.Heading", background=BG3, foreground=FG_DIM,
                    font=FONT_B, borderwidth=0, relief="flat")
        s.map("Treeview", background=[("selected", "#2d4f7c")],
              foreground=[("selected", "#ffffff")])
        s.configure("Vertical.TScrollbar", background=BG3, troughcolor=BG,
                    borderwidth=0, arrowcolor=FG_DIM)

    def _chip(self, parent, text, var, value, cmd):
        # fg must stay light: when selected the background becomes ACCENT,
        # and FG_DIM text on blue is unreadable.
        b = tk.Radiobutton(
            parent, text=text, variable=var, value=value, command=cmd,
            indicatoron=False, bg=BG3, fg=FG, selectcolor=ACCENT,
            activebackground=BG3, activeforeground=FG, font=FONT,
            bd=0, relief="flat", padx=13, pady=6, cursor="hand2",
            highlightthickness=0)
        b.pack(side="left", padx=(0, 6))
        return b

    def _btn(self, parent, text, cmd, kind="normal"):
        colors = {"normal": (BG3, FG), "primary": (ACCENT, "#ffffff"),
                  "danger": ("#3d2326", BAD)}
        bg, fg = colors[kind]
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=bg, activeforeground=fg,
                      font=FONT_B, bd=0, relief="flat", padx=16, pady=9,
                      cursor="hand2", highlightthickness=0)
        return b

    # ------------------------------------------------------------- search

    def _build_search(self, root):
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=22, pady=(18, 0))

        tk.Label(top, text="Search everything on this PC", bg=BG, fg=FG,
                 font=FONT_H).pack(anchor="w")

        ebox = tk.Frame(top, bg=BG3)
        ebox.pack(fill="x", pady=(12, 0))
        self.entry = tk.Entry(ebox, bg=BG3, fg=FG, font=("Segoe UI", 14),
                              bd=0, relief="flat", insertbackground=ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, padx=14, pady=11)
        self.entry.bind("<KeyRelease>", self._typed)
        self.entry.bind("<Return>", lambda e: self._search(now=True))
        self.entry.bind("<Down>", self._focus_results)

        clear = tk.Label(ebox, text="  X  ", bg=BG3, fg=FG_DIM, font=FONT,
                         cursor="hand2")
        clear.pack(side="right", padx=(0, 8))
        clear.bind("<Button-1>", lambda e: self._clear())

        # mode + filters
        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(12, 0))

        self.mode = tk.StringVar(value="names")
        self._chip(row, "File names", self.mode, "names", self._search)
        self._chip(row, "Inside documents", self.mode, "content", self._search)

        tk.Frame(row, bg=BG3, width=1, height=26).pack(side="left", padx=12)

        # Documents is the default: searching "bank" with no filter otherwise
        # buries the statements under scripts and config files.
        DOCS = "pdf,xlsx,xlsm,docx,doc,csv,pptx"
        self.kind = tk.StringVar(value=DOCS)
        for label, val in (("Documents", DOCS), ("PDF", "pdf"),
                           ("Excel", "xlsx,xlsm,csv"), ("Word", "docx"),
                           ("Images", "png,jpg,jpeg,heic"),
                           ("Everything", "all")):
            self._chip(row, label, self.kind, val, self._search)

        tk.Frame(row, bg=BG3, width=1, height=26).pack(side="left", padx=12)

        self.recent = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Last 30 days", variable=self.recent,
                       command=self._search, bg=BG, fg=FG_DIM,
                       selectcolor=BG3, activebackground=BG,
                       activeforeground=FG, font=FONT, bd=0,
                       highlightthickness=0, cursor="hand2").pack(side="left")

        # Bottom-anchored widgets are packed FIRST so they always reserve
        # their height; the results table then takes whatever is left.
        bot = tk.Frame(root, bg=BG)
        bot.pack(side="bottom", fill="x", padx=22, pady=(10, 14))

        self.preview = tk.Label(root, text="", bg=BG, fg="#9fd69f",
                                font=FONT, anchor="w", justify="left",
                                wraplength=1100, height=2)
        self.preview.pack(side="bottom", fill="x", padx=24)

        # results
        mid = tk.Frame(root, bg=BG)
        mid.pack(fill="both", expand=True, padx=22, pady=(14, 0))

        cols = ("name", "where", "size", "when")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings",
                                 selectmode="browse")
        self.tree.heading("name", text="  NAME")
        self.tree.heading("where", text="FOLDER")
        self.tree.heading("size", text="SIZE")
        self.tree.heading("when", text="MODIFIED")
        self.tree.column("name", width=430, anchor="w")
        self.tree.column("where", width=460, anchor="w")
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("when", width=130, anchor="e")
        self.tree.tag_configure("snip", foreground="#9fd69f")

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self._open_file())
        self.tree.bind("<Return>", lambda e: self._open_file())
        self.tree.bind("<Button-3>", self._context)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self._btn(bot, "Open", self._open_file, "primary").pack(side="left")
        self._btn(bot, "Show in folder", self._open_folder).pack(side="left", padx=(8, 0))
        self._btn(bot, "Copy path", self._copy_path).pack(side="left", padx=(8, 0))

        self.status = tk.Label(bot, text="", bg=BG, fg=FG_DIM, font=FONT)
        self.status.pack(side="right")

        tk.Label(bot, text="Double-click a result to open it",
                 bg=BG, fg=FG_DIM, font=FONT).pack(side="left", padx=(16, 0))

        self.menu = tk.Menu(self, tearoff=0, bg=BG2, fg=FG,
                            activebackground=ACCENT, activeforeground="#fff",
                            bd=0)
        self.menu.add_command(label="Open", command=self._open_file)
        self.menu.add_command(label="Show in folder", command=self._open_folder)
        self.menu.add_command(label="Copy path", command=self._copy_path)

    def _clear(self):
        self.entry.delete(0, "end")
        self._search()

    def _focus_results(self, _e=None):
        kids = self.tree.get_children()
        if kids:
            self.tree.focus_set()
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])
        return "break"

    def _typed(self, e):
        if e.keysym in ("Up", "Down", "Return", "Escape"):
            return
        if self.after_id:
            self.after_cancel(self.after_id)
        self.after_id = self.after(180, self._search)

    def _search(self, now=False):
        self.after_id = None
        q = self.entry.get().strip()
        mode = self.mode.get()
        if mode == "content" and len(q) < 2:
            self._render([], "Type at least 2 characters to search inside documents.")
            return
        if mode == "names" and not q and not self.recent.get():
            self._render([], "Start typing, or tick \"Last 30 days\" to see "
                             "what you worked on recently.")
            return

        self.seq += 1
        seq = self.seq
        ext = None if self.kind.get() == "all" else self.kind.get()
        newer = "30d" if self.recent.get() else None
        self.status.configure(text="searching...")

        def work():
            try:
                con = D.connect(DB_PATH)
                try:
                    if mode == "content":
                        rows = S.grep(con, q, ext=ext, newer=newer, limit=120)
                    else:
                        rows = S.find(con, q, ext=ext, newer=newer, limit=200)
                finally:
                    con.close()
                self.q.put(("results", seq, rows, None))
            except Exception as ex:
                self.q.put(("results", seq, [], str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _render(self, rows, msg=None):
        self.tree.delete(*self.tree.get_children())
        self.rows = rows
        self.preview.configure(text="")
        for i, r in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), values=(
                "  " + r["name"],
                r["dirpath"],
                human(r["size"]),
                ago(r["mtime"]),
            ))
        if msg:
            self.status.configure(text=msg)
        else:
            self.status.configure(
                text="%d result%s" % (len(rows), "" if len(rows) == 1 else "s"))

    def _on_select(self, _e=None):
        r = self._sel()
        if r and r.get("snip"):
            self.preview.configure(text=r["snip"][:400].replace(">>", "[").replace("<<", "]"))
        else:
            self.preview.configure(text="")

    def _sel(self):
        s = self.tree.selection()
        if not s:
            return None
        try:
            return self.rows[int(s[0])]
        except (ValueError, IndexError):
            return None

    def _context(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(e.x_root, e.y_root)

    def _open_file(self):
        r = self._sel()
        if not r:
            return
        if not os.path.exists(r["path"]):
            messagebox.showwarning(
                "Not there any more",
                "This file has been moved or deleted since the last index.\n\n"
                + r["path"] + "\n\nUse PC Health > Refresh index to update.")
            return
        try:
            os.startfile(r["path"])
        except Exception as ex:
            messagebox.showerror("Could not open", str(ex))

    def _open_folder(self):
        r = self._sel()
        if not r:
            return
        if os.path.exists(r["path"]):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(r["path"])])
        elif os.path.isdir(r["dirpath"]):
            subprocess.Popen(["explorer", os.path.normpath(r["dirpath"])])
        else:
            messagebox.showwarning("Not there any more", r["dirpath"])

    def _copy_path(self):
        r = self._sel()
        if not r:
            return
        self.clipboard_clear()
        self.clipboard_append(r["path"])
        self.status.configure(text="path copied")

    # ------------------------------------------------------------- health

    def _build_health(self, root):
        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=18)

        tk.Label(wrap, text="PC Health", bg=BG, fg=FG,
                 font=FONT_H).pack(anchor="w")
        tk.Label(wrap, text="What is slowing this machine down, and what to do about it.",
                 bg=BG, fg=FG_DIM, font=FONT).pack(anchor="w", pady=(2, 14))

        # Bottom-anchored first so the buttons always keep their space, no
        # matter how many findings the body ends up listing.
        self.health_status = tk.Label(wrap, text="", bg=BG, fg=FG_DIM,
                                      font=FONT, anchor="w")
        self.health_status.pack(side="bottom", fill="x", pady=(8, 0))

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(side="bottom", fill="x", pady=(14, 0))
        self._btn(bar, "Speed up my PC", self._do_tune, "primary").pack(side="left")
        self._btn(bar, "Undo those changes", self._do_restore, "danger").pack(side="left", padx=(8, 0))
        self._btn(bar, "Refresh index", self._do_refresh).pack(side="left", padx=(8, 0))
        self._btn(bar, "Check again", self._load_health).pack(side="left", padx=(8, 0))

        # The findings list can outgrow the window, so it scrolls.
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self.health_body = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=self.health_body, anchor="nw")
        self.health_body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
            if self.nb.index("current") == 1 else None)

        self._health_loaded = False

    def _tab_changed(self, _e=None):
        if self.nb.index("current") == 1 and not self._health_loaded:
            self._health_loaded = True
            self._load_health()

    def _load_health(self):
        for w in self.health_body.winfo_children():
            w.destroy()
        tk.Label(self.health_body, text="Checking...", bg=BG, fg=FG_DIM,
                 font=FONT).pack(anchor="w")

        def work():
            try:
                import doctor
                con = D.connect(DB_PATH)
                try:
                    cfg = {"roots": []}
                    d = doctor.collect(cfg, con)
                    v = doctor.verdicts(d)
                finally:
                    con.close()
                self.q.put(("health", 0, (d, v), None))
            except Exception as ex:
                self.q.put(("health", 0, None, str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _render_health(self, d, verdicts):
        for w in self.health_body.winfo_children():
            w.destroy()
        body = self.health_body

        def meter(label, pct, detail, col):
            f = tk.Frame(body, bg=BG)
            f.pack(fill="x", pady=(0, 9))
            tk.Label(f, text=label, bg=BG, fg=FG_DIM, font=FONT_B,
                     width=8, anchor="w").pack(side="left")
            track = tk.Frame(f, bg=BG3, height=22, width=360)
            track.pack(side="left")
            track.pack_propagate(False)
            fill = tk.Frame(track, bg=col, height=22,
                            width=max(2, int(360 * min(pct, 100) / 100)))
            fill.place(x=0, y=0)
            tk.Label(f, text=detail, bg=BG, fg=FG, font=FONT).pack(
                side="left", padx=(12, 0))

        r = d.get("ram")
        if r:
            pct = r["pct"]
            col = BAD if pct > 88 else (WARN if pct > 70 else GOOD)
            meter("Memory", pct, "%s free of %s"
                  % (human(r["available"]), human(r["total"])), col)
        if d.get("cpu_pct") is not None:
            cp = d["cpu_pct"]
            col = BAD if cp > 85 else (WARN if cp > 55 else GOOD)
            meter("CPU", cp, "%.0f%% busy" % cp, col)
        for dk in d.get("disks", []):
            col = BAD if dk["pct"] > 90 else (WARN if dk["pct"] > 80 else GOOD)
            meter("Drive " + dk["drive"], dk["pct"],
                  "%s free" % human(dk["free"]), col)

        # apps using memory
        tk.Label(body, text="Using the most memory right now", bg=BG,
                 fg=FG_DIM, font=FONT_B).pack(anchor="w", pady=(14, 6))
        apps = tk.Frame(body, bg=BG)
        apps.pack(fill="x")
        for p in d.get("procs", [])[:6]:
            if not p["label"]:
                continue
            row = tk.Frame(apps, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=p["label"], bg=BG, fg=FG, font=FONT,
                     width=18, anchor="w").pack(side="left")
            tk.Label(row, text=human(p["rss"]), bg=BG, fg=WARN, font=FONT_B,
                     width=10, anchor="w").pack(side="left")
            tk.Label(row, text="%d windows/processes" % p["count"], bg=BG,
                     fg=FG_DIM, font=FONT).pack(side="left")

        # findings
        tk.Label(body, text="What to do", bg=BG, fg=FG_DIM,
                 font=FONT_B).pack(anchor="w", pady=(16, 6))
        for sev, head, detail in verdicts:
            col = {"crit": BAD, "warn": WARN, "ok": GOOD}[sev]
            f = tk.Frame(body, bg=BG)
            f.pack(fill="x", pady=2)
            tk.Label(f, text="*", bg=BG, fg=col, font=FONT_B,
                     width=2).pack(side="left", anchor="n")
            t = tk.Frame(f, bg=BG)
            t.pack(side="left", fill="x", expand=True)
            tk.Label(t, text=head, bg=BG, fg=FG, font=FONT_B,
                     anchor="w", justify="left").pack(anchor="w")
            if detail:
                tk.Label(t, text=detail, bg=BG, fg=FG_DIM, font=FONT,
                         anchor="w", justify="left",
                         wraplength=940).pack(anchor="w")

    # -------------------------------------------------------- health acts

    def _run_async(self, title, args, elevate=False, done_msg=None):
        self.health_status.configure(text=title + " - running...")

        def work():
            try:
                if elevate:
                    ps = ("Start-Process powershell -Verb RunAs -Wait "
                          "-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass',"
                          "'-File','%s','%s'" % (
                              os.path.join(HERE, "tune.ps1").replace("'", "''"),
                              args))
                    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                   capture_output=True, text=True, timeout=900)
                else:
                    subprocess.run(args, capture_output=True, text=True,
                                   timeout=1800)
                self.q.put(("done", 0, done_msg or (title + " - finished"), None))
            except Exception as ex:
                self.q.put(("done", 0, None, str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _do_tune(self):
        if not messagebox.askyesno(
                "Speed up my PC",
                "This will:\n\n"
                "  - put the CPU on its fastest power setting\n"
                "  - cut window animations\n"
                "  - stop the antivirus rescanning your work folders\n"
                "  - turn on TRIM so the SSD stays quick\n\n"
                "Everything is backed up first. You can undo it any time with "
                "\"Undo those changes\".\n\n"
                "Windows will ask for permission - click Yes.\n\nGo ahead?"):
            return
        self._run_async("Speeding up", "apply -Yes", elevate=True,
                        done_msg="Done. Sign out and back in for the "
                                 "animation change to fully apply.")

    def _do_restore(self):
        if not messagebox.askyesno(
                "Undo those changes",
                "This puts every setting back the way it was before PULSE "
                "changed it.\n\nWindows will ask for permission - click Yes."
                "\n\nGo ahead?"):
            return
        self._run_async("Undoing", "restore", elevate=True,
                        done_msg="All settings restored.")

    def _do_refresh(self):
        self._run_async(
            "Refreshing index",
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File",
             os.path.join(HERE, "refresh.ps1")],
            done_msg="Index refreshed.")

    # --------------------------------------------------------------- misc

    def _load_stats(self):
        def work():
            try:
                con = D.connect(DB_PATH)
                try:
                    n = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
                finally:
                    con.close()
                self.q.put(("stats", 0, n, None))
            except Exception:
                self.q.put(("stats", 0, 0, None))

        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                kind, seq, payload, err = self.q.get_nowait()
                if kind == "results":
                    if seq != self.seq:
                        continue          # a newer search already went out
                    if err:
                        self._render([], "Search failed: " + err)
                    else:
                        self._render(payload)
                elif kind == "health":
                    if err:
                        for w in self.health_body.winfo_children():
                            w.destroy()
                        tk.Label(self.health_body,
                                 text="Could not read health info: " + err,
                                 bg=BG, fg=BAD, font=FONT).pack(anchor="w")
                    else:
                        self._render_health(*payload)
                elif kind == "stats":
                    self._render([], "Start typing to search %s files."
                                 % format(payload, ","))
                elif kind == "show":
                    self.surface()
                elif kind == "done":
                    if err:
                        self.health_status.configure(text="Failed: " + err)
                    else:
                        self.health_status.configure(text=payload)
                        self._load_health()
        except queue.Empty:
            pass
        self.after(80, self._pump)


if __name__ == "__main__":
    if already_running():
        sys.exit(0)         # the running copy has been told to surface
    if not os.path.exists(DB_PATH):
        import tkinter.messagebox as mb
        root = tk.Tk()
        root.withdraw()
        mb.showerror("PULSE",
                     "No index yet.\n\nRun 'Refresh index' first, or ask "
                     "Claude to build it.")
        sys.exit(1)
    app = Pulse()
    # --query "..." pre-fills the box (used for testing the layout without
    # sending keystrokes to whatever window happens to be focused).
    if "--query" in sys.argv:
        i = sys.argv.index("--query")
        if i + 1 < len(sys.argv):
            app.entry.insert(0, sys.argv[i + 1])
            app.after(400, lambda: app._search(now=True))
    if "--health" in sys.argv:
        app.nb.select(1)
    app.mainloop()
