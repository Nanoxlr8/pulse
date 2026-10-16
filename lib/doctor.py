"""PULSE doctor - live health readout for this PC."""
import os
import sys
import json
import time
import shutil
import subprocess

try:
    import psutil
except ImportError:
    psutil = None

# Processes that are safe to describe as "closeable" - user-facing apps that
# routinely hold a lot of RAM. Never auto-killed; doctor only reports.
HEAVY_APPS = {
    "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "slack": "Slack", "teams": "Teams", "ms-teams": "Teams",
    "chatgpt": "ChatGPT", "claude": "Claude", "code": "VS Code",
    "discord": "Discord", "spotify": "Spotify", "whatsapp": "WhatsApp",
    "outlook": "Outlook", "excel": "Excel", "winword": "Word",
    "googledrivefs": "Google Drive", "onedrive": "OneDrive",
    "antigravity": "Antigravity", "cursor": "Cursor",
}


def _ps(cmd):
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                              "-Command", cmd],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception:
        return ""


def collect(cfg, con):
    d = {"ts": time.time()}

    if psutil:
        vm = psutil.virtual_memory()
        d["ram"] = {"total": vm.total, "available": vm.available,
                    "used": vm.used, "pct": vm.percent}
        sw = psutil.swap_memory()
        d["swap"] = {"total": sw.total, "used": sw.used, "pct": sw.percent}
        d["cpu_pct"] = psutil.cpu_percent(interval=0.6)
        d["cpu_count"] = psutil.cpu_count(logical=True)
        try:
            bat = psutil.sensors_battery()
            d["battery"] = {"pct": bat.percent, "plugged": bat.power_plugged} \
                if bat else None
        except Exception:
            d["battery"] = None

        # group processes by base name
        groups = {}
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                nm = (p.info["name"] or "").lower().replace(".exe", "")
                mi = p.info["memory_info"]
                if not mi:
                    continue
                g = groups.setdefault(nm, {"rss": 0, "n": 0})
                g["rss"] += mi.rss
                g["n"] += 1
            except Exception:
                continue
        top = sorted(groups.items(), key=lambda kv: -kv[1]["rss"])[:14]
        d["procs"] = [{"name": k, "rss": v["rss"], "count": v["n"],
                       "label": HEAVY_APPS.get(k)} for k, v in top]
    else:
        d["ram"] = None
        d["procs"] = []

    # disks
    d["disks"] = []
    for root in cfg.get("roots", []):
        pass
    for letter in "CDEFGHI":
        p = letter + ":\\"
        if os.path.isdir(p):
            try:
                u = shutil.disk_usage(p)
                d["disks"].append({"drive": letter, "total": u.total,
                                   "free": u.free,
                                   "pct": 100.0 * u.used / u.total})
            except Exception:
                continue

    # One PowerShell call for everything Windows-side. Three separate
    # subprocess spawns cost 5-10s, which is long enough that the Health tab
    # looks hung.
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "'@@PLAN'; (powercfg /getactivescheme);"
        "'@@STARTUP'; (Get-CimInstance Win32_StartupCommand | Measure-Object).Count;"
        "'@@SLOTS'; Get-CimInstance Win32_PhysicalMemory | ForEach-Object "
        "{ '{0}|{1}|{2}|{3}' -f $_.DeviceLocator, $_.Capacity, $_.Speed, "
        "(''+$_.PartNumber).Trim() }"
    )
    plan, startup, slots = "", 0, []
    try:
        raw = _ps(script)
        section = None
        for line in raw.splitlines():
            t = line.strip()
            if t in ("@@PLAN", "@@STARTUP", "@@SLOTS"):
                section = t
                continue
            if not t:
                continue
            if section == "@@PLAN":
                plan = t
            elif section == "@@STARTUP":
                try:
                    startup = int(t)
                except ValueError:
                    pass
            elif section == "@@SLOTS":
                parts = t.split("|")
                if len(parts) == 4 and parts[1].isdigit():
                    slots.append({"locator": parts[0], "bytes": int(parts[1]),
                                  "speed": parts[2], "part": parts[3],
                                  "soldered": not parts[3]})
    except Exception:
        pass
    d["power_plan"] = plan
    d["startup"] = startup
    d["slots"] = slots

    # index freshness
    try:
        import db as D
        li = D.get_meta(con, "last_index")
        n = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        d["index"] = {"files": n, "last": int(li) if li else None}
    except Exception:
        d["index"] = None

    return d


def verdicts(d):
    """-> list of (severity, headline, detail). severity: crit|warn|ok"""
    v = []
    r = d.get("ram")
    if r:
        free_gb = r["available"] / (1 << 30)
        if free_gb < 1.5:
            v.append(("crit", "RAM exhausted - %.1f GB free of %.0f GB"
                      % (free_gb, r["total"] / (1 << 30)),
                      "Windows is paging to disk. This is what makes the PC "
                      "feel slow. Close the heaviest apps listed below."))
        elif free_gb < 3.5:
            v.append(("warn", "RAM tight - %.1f GB free" % free_gb,
                      "Headroom is thin; a big Excel file or a new Chrome "
                      "window will push it into paging."))
        else:
            v.append(("ok", "RAM healthy - %.1f GB free" % free_gb, ""))

    slots = d.get("slots") or []
    if slots and r:
        swappable = [s for s in slots if not s["soldered"]]
        if swappable and (r["total"] / (1 << 30)) < 24:
            biggest = max(swappable, key=lambda s: s["bytes"])
            v.append(("warn", "RAM is upgradeable - %d of %d modules is a socketed SODIMM"
                      % (len(swappable), len(slots)),
                      "%s holds %.0f GB (%s MHz). Swapping that one stick for a "
                      "32 GB DDR4-%s SODIMM would take this machine to ~%.0f GB "
                      "and is the only real fix for the paging above."
                      % (biggest["locator"], biggest["bytes"] / (1 << 30),
                         biggest["speed"], biggest["speed"],
                         (r["total"] - biggest["bytes"] + (32 << 30)) / (1 << 30))))

    sw = d.get("swap")
    if sw and sw["total"]:
        used_gb = sw["used"] / (1 << 30)
        if used_gb > 4:
            v.append(("warn", "Pagefile in heavy use - %.1f GB" % used_gb,
                      "Confirms memory pressure; the SSD is standing in "
                      "for RAM."))

    pp = (d.get("power_plan") or "").lower()
    if "balanced" in pp:
        v.append(("warn", "Power plan is Balanced",
                  "On this CPU that caps sustained speed. "
                  'Click "Speed up my PC" below to fix it.'))
    elif "ultimate" in pp or "high performance" in pp:
        v.append(("ok", "Power plan is set for performance", ""))

    if d.get("startup", 0) >= 12:
        v.append(("warn", "%d programs start automatically with Windows"
                  % d["startup"],
                  "Each one costs boot time and memory. To review them: "
                  "right-click the taskbar, open Task Manager, then the "
                  '"Startup apps" tab. Turn off anything you do not '
                  "recognise or use daily."))

    for dk in d.get("disks", []):
        free_gb = dk["free"] / (1 << 30)
        if dk["pct"] > 90:
            v.append(("crit", "Drive %s: %.0f%% full (%.0f GB free)"
                      % (dk["drive"], dk["pct"], free_gb),
                      "An NVMe SSD slows down and wears faster above 90%."))
        elif dk["pct"] > 80:
            v.append(("warn", "Drive %s: %.0f%% full (%.0f GB free)"
                      % (dk["drive"], dk["pct"], free_gb), ""))

    idx = d.get("index")
    if idx:
        if not idx["files"]:
            v.append(("warn", "No files indexed yet",
                      'Click "Refresh index" below to build it.'))
        elif idx["last"] and (time.time() - idx["last"]) > 3 * 86400:
            v.append(("warn", "File list is %.0f days out of date"
                      % ((time.time() - idx["last"]) / 86400),
                      'Click "Refresh index" below. It updates daily on its '
                      "own, so this usually means the PC was off."))
        else:
            v.append(("ok", "%s files ready to search"
                      % format(idx["files"], ","), ""))

    return v


def run(a, cfg, con, C, human):
    d = collect(cfg, con)
    if getattr(a, "json", False):
        d["verdicts"] = [{"severity": s, "headline": h, "detail": t}
                         for s, h, t in verdicts(d)]
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0

    SEV = {"crit": (C["r"], "FAIL"), "warn": (C["y"], "WARN"),
           "ok": (C["g"], " OK ")}

    print()
    print(C["b"] + "  PULSE DOCTOR" + C["x"] + "   "
          + C["d"] + time.strftime("%Y-%m-%d %H:%M") + C["x"])
    print(C["d"] + "  " + "-" * 64 + C["x"])

    r = d.get("ram")
    if r:
        used_pct = r["pct"]
        width = 40
        fill = int(width * used_pct / 100)
        col = C["r"] if used_pct > 88 else (C["y"] if used_pct > 70 else C["g"])
        bar = col + "#" * fill + C["d"] + "." * (width - fill) + C["x"]
        print("  RAM   [%s] %5.1f%%   %s free of %s"
              % (bar, used_pct, human(r["available"]), human(r["total"])))

    if d.get("cpu_pct") is not None:
        cp = d["cpu_pct"]
        width = 40
        fill = int(width * cp / 100)
        col = C["r"] if cp > 85 else (C["y"] if cp > 55 else C["g"])
        bar = col + "#" * fill + C["d"] + "." * (width - fill) + C["x"]
        print("  CPU   [%s] %5.1f%%   %d threads" % (bar, cp, d["cpu_count"]))

    print()
    for dk in d.get("disks", []):
        width = 20
        fill = int(width * dk["pct"] / 100)
        col = C["r"] if dk["pct"] > 90 else (C["y"] if dk["pct"] > 80 else C["g"])
        bar = col + "#" * fill + C["d"] + "." * (width - fill) + C["x"]
        print("  %s:    [%s] %4.0f%%  %s free"
              % (dk["drive"], bar, dk["pct"], human(dk["free"])))

    slots = d.get("slots") or []
    if slots:
        print()
        print(C["b"] + "  MEMORY SLOTS" + C["x"])
        for s in slots:
            kind = (C["y"] + "soldered" + C["x"]) if s["soldered"] \
                else (C["g"] + "socketed" + C["x"])
            print("    %-28s %6s  %s MHz  %s  %s%s%s"
                  % (s["locator"], human(s["bytes"]), s["speed"], kind,
                     C["d"], s["part"] or "-", C["x"]))

    print()
    print(C["b"] + "  MEMORY BY APP" + C["x"])
    for p in d.get("procs", [])[:10]:
        label = p["label"] or p["name"]
        tag = "" if p["label"] else C["d"] + " (system)" + C["x"]
        print("    %-18s %8s  %s%d process%s%s"
              % (label, human(p["rss"]), C["d"], p["count"],
                 "" if p["count"] == 1 else "es", C["x"] + tag))

    print()
    print(C["b"] + "  FINDINGS" + C["x"])
    for sev, head, detail in verdicts(d):
        col, tag = SEV[sev]
        print("    %s[%s]%s %s" % (col, tag, C["x"], head))
        if detail:
            print("           " + C["d"] + detail + C["x"])
    print()
    return 0
