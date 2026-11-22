#!/usr/bin/env python3
"""eqctl.py - pilote des profils Claude pour Equalizer APO.

Source de verite : claude_profiles/_state.json
Fichiers generes  : claude_active.txt (routeur) et claude_profiles/_live_adjust.txt
                    (ajustements live + compensation exacte du Preamp casque).

Usage :
  python eqctl.py pas|clutch|chill|solo      profil casque (remet les ajustements a 0)
  python eqctl.py devices                    liste tes sorties audio Windows (numerotees)
  python eqctl.py setdevice headset|speakers N   choisit la sortie du casque / des enceintes
  python eqctl.py enceintes                  profil enceintes propre
  python eqctl.py nuit                       profil nuit sur les enceintes
  python eqctl.py mal                        -2.5 dB sur 3-7 kHz (cumulatif, casque)
  python eqctl.py plusdepas                  +1.5 dB sur les frequences de pas (casque)
  python eqctl.py adj FC GAIN Q [note]       ajout d'un filtre PK live (casque)
  python eqctl.py undo                       retire le dernier ajustement adj
  python eqctl.py clearadj                   efface tous les ajustements live
  python eqctl.py bypass [on|off|toggle]     comparaison A/B (son d'origine, etat conserve)
  python eqctl.py save                       fige profil + ajustements dans 07_perso.txt
  python eqctl.py perso                      active 07_perso.txt
  python eqctl.py spkneutre                  enceintes en neutre
  python eqctl.py musique                    profil casque musique (08_musique)
  python eqctl.py spkmusique|spkcinema       enceintes musique / cinema
  python eqctl.py reset                      tout en neutre / bypass
  python eqctl.py status                     etat en 3 lignes
  python eqctl.py fixpreamp                  recalcule le Preamp propre a chaque profil
  python eqctl.py analyze FICHIER            detail d'une courbe (suit les Include)
  python eqctl.py curve                      gains de la chaine casque active par bande
"""
import cmath
import json
import math
import re
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent
PROFILES = CONFIG / "claude_profiles"
ROUTER = CONFIG / "claude_active.txt"
OVERLAY = PROFILES / "_live_adjust.txt"
STATE = PROFILES / "_state.json"
FS = 48000.0  # format du peripherique casque et enceintes (48 kHz)

ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
DEVICES_FILE = PROFILES / "devices.json"


def load_devices():
    """Sorties audio utilisees : lues dans claude_profiles/devices.json (cree par `setdevice` ou setup.py).
    Tant qu'une sortie n'est pas choisie, elle est ignoree (aucune ligne Device invalide n'est ecrite)."""
    d = {"headset": [ZERO_GUID, "CASQUE (non configure)"],
         "speakers": [ZERO_GUID, "ENCEINTES (non configure)"]}
    try:
        for k, v in json.loads(DEVICES_FILE.read_text(encoding="utf-8")).items():
            if k in d and isinstance(v, list) and len(v) == 2:
                d[k] = v
    except (OSError, ValueError):
        pass
    return {k: tuple(v) for k, v in d.items()}


DEVICES = load_devices()


def list_render_devices():
    """Sorties audio actives de Windows : [(guid, nom)] lues dans le registre (aucun droit admin requis)."""
    import winreg
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    out = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
        for i in range(winreg.QueryInfoKey(k)[0]):
            g = winreg.EnumKey(k, i)
            try:
                with winreg.OpenKey(k, g) as dk:
                    if winreg.QueryValueEx(dk, "DeviceState")[0] != 1:
                        continue
                with winreg.OpenKey(k, g + r"\Properties") as pk:
                    name = winreg.QueryValueEx(pk, "{a45c254e-df1c-4efd-8020-67d146a850e0},2")[0]
                    try:
                        name += " (%s)" % winreg.QueryValueEx(pk, "{b3f8fa53-0004-438e-9003-51a46e139bfc},6")[0]
                    except OSError:
                        pass
                out.append(("{%s}" % g.strip("{}"), name))
            except OSError:
                continue
    return out


def set_device(role, guid, name):
    cur = {k: list(v) for k, v in DEVICES.items()}
    cur[role] = [guid, name]
    PROFILES.mkdir(exist_ok=True)
    DEVICES_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
ALIASES = {
    "pas": ("headset", "01_wallhack_radar.txt"),
    "clutch": ("headset", "02_clutch_surgeon.txt"),
    "chill": ("headset", "03_long_session_comfort.txt"),
    "solo": ("headset", "04_cinematic_solo.txt"),
    "enceintes": ("speakers", "05_speakers_clean.txt"),
    "nuit": ("speakers", "06_speakers_night_mode.txt"),
    "perso": ("headset", "07_perso.txt"),
    "spkneutre": ("speakers", "neutral.txt"),
    # 2026-11-21 : modes MUSIQUE et CINEMA du PC Command HUD
    "musique": ("headset", "08_musique.txt"),
    "spkmusique": ("speakers", "09_speakers_music.txt"),
    "spkcinema": ("speakers", "10_speakers_cinema.txt"),
}
NEUTRAL = "neutral.txt"
PERSO = "07_perso.txt"
DEFAULT_STATE = {"headset": NEUTRAL, "speakers": NEUTRAL,
                 "mal_db": 0.0, "plus_db": 0.0, "extra": [], "bypass": False}
MAL = {"fc": 4583.0, "q": 1.2, "step": -2.5, "floor": -10.0}   # 3-7 kHz (centre geometrique)
PLUS = {"q": 2.2, "step": 1.5, "ceil": 6.0}
DEFAULT_FOOTSTEPS = [2400.0, 4300.0]                           # Arma Reforger

RE_PREAMP = re.compile(r"^\s*Preamp\s*:\s*(-?[\d.,]+)\s*dB", re.I)
RE_FILTER = re.compile(r"^\s*Filter(?:\s*\d+)?\s*:\s*ON\s+([A-Z]+)\b(.*)$", re.I)
RE_INCLUDE = re.compile(r"^\s*Include\s*:\s*(.+?)\s*$", re.I)
RE_CHANNEL = re.compile(r"^\s*Channel\s*:\s*(.+?)\s*$", re.I)
RE_XFEED = re.compile(r"#\s*@crossfeed\s+(-?[\d.]+)\s+([\d.]+)", re.I)
RE_STEPS = re.compile(r"#\s*@footsteps\s+([\d.\s]+)", re.I)


def num(s):
    return float(s.replace(",", "."))


# ---------------------------------------------------------------- DSP
def biquad(kind, fc, gain=0.0, q=0.7071):
    A = 10 ** (gain / 40.0)
    w0 = 2 * math.pi * fc / FS
    cw, sw = math.cos(w0), math.sin(w0)
    al = sw / (2 * q)
    if kind == "PK":
        b = [1 + al * A, -2 * cw, 1 - al * A]
        a = [1 + al / A, -2 * cw, 1 - al / A]
    elif kind == "LSC":
        s = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw),
             A * ((A + 1) - (A - 1) * cw - s)]
        a = [(A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw),
             (A + 1) + (A - 1) * cw - s]
    elif kind == "HSC":
        s = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw),
             A * ((A + 1) + (A - 1) * cw - s)]
        a = [(A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw),
             (A + 1) - (A - 1) * cw - s]
    elif kind == "LPQ":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    elif kind == "HPQ":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    else:
        raise ValueError(kind)
    return b, a


def mag_db(coefs, f):
    b, a = coefs
    z1 = cmath.exp(-1j * 2 * math.pi * f / FS)
    h = (b[0] + b[1] * z1 + b[2] * z1 * z1) / (a[0] + a[1] * z1 + a[2] * z1 * z1)
    return 20 * math.log10(max(abs(h), 1e-12))


KIND_MAP = {"PK": "PK", "PEQ": "PK", "LSC": "LSC", "LS": "LSC", "HSC": "HSC", "HS": "HSC",
            "LPQ": "LPQ", "LP": "LPQ", "HPQ": "HPQ", "HP": "HPQ"}


def parse_filter(kind, rest):
    k = KIND_MAP.get(kind.upper())
    if k is None:
        raise ValueError("type de filtre non gere par l'analyseur : " + kind)
    fc = num(re.search(r"Fc\s+([\d.,]+)", rest, re.I).group(1))
    g = re.search(r"Gain\s+(-?[\d.,]+)", rest, re.I)
    q = re.search(r"\bQ\s+([\d.,]+)", rest, re.I)
    return (k, fc, num(g.group(1)) if g else 0.0, num(q.group(1)) if q else 0.7071)


def parse_file(path, follow=True, _seen=None):
    """Retourne dict(preamps, filters, xfeeds, steps). Ne compte que les filtres
    appliques au canal L (ignore les canaux virtuels du crossfeed)."""
    path = Path(path)
    _seen = _seen or set()
    if path.resolve() in _seen:
        raise RuntimeError("Include circulaire : %s" % path)
    _seen.add(path.resolve())
    out = {"preamps": [], "filters": [], "xfeeds": [], "steps": None}
    on_main = True
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = RE_XFEED.search(line)
        if m:
            out["xfeeds"].append((num(m.group(1)), num(m.group(2))))
        m = RE_STEPS.search(line)
        if m:
            out["steps"] = [num(x) for x in m.group(1).split()]
        code = line.split("#", 1)[0]
        if not code.strip():
            continue
        if m := RE_CHANNEL.match(code):
            toks = m.group(1).replace(";", " ").split()
            on_main = any(t.lower() == "all" or t.upper() == "L" for t in toks)
        elif m := RE_PREAMP.match(code):
            out["preamps"].append(num(m.group(1)))
        elif m := RE_FILTER.match(code):
            if on_main:
                out["filters"].append(parse_filter(m.group(1), m.group(2)))
        elif (m := RE_INCLUDE.match(code)) and follow:
            sub = parse_file(path.parent / m.group(1), True, _seen)
            for key in ("preamps", "filters", "xfeeds"):
                out[key] += sub[key]
            out["steps"] = out["steps"] or sub["steps"]
    return out


GRID = [20 * 2 ** (i / 48.0) for i in range(int(48 * math.log2(20000 / 20)) + 1)]


def response(filters, xfeeds=()):
    co = [biquad(k, fc, g, q) for (k, fc, g, q) in filters]
    xf = [(10 ** (g / 20.0), biquad("LPQ", fc, 0, 0.71)) for (g, fc) in xfeeds]
    curve = []
    for f in GRID:
        db = sum(mag_db(c, f) for c in co)
        for lin, lp in xf:  # pire cas : signal mono en phase
            db += 20 * math.log10(1 + lin * 10 ** (mag_db(lp, f) / 20.0))
        curve.append(db)
    return curve


def ceil1(x):
    return math.ceil(x * 10 - 1e-9) / 10.0


def needed_preamp(curve):
    return -ceil1(max(0.0, max(curve))) + 0.0  # + 0.0 : evite l'affichage "-0.0"


def top_peaks(curve, n=3):
    peaks = [(curve[i], GRID[i]) for i in range(1, len(curve) - 1)
             if curve[i] > 0.05 and curve[i] >= curve[i - 1] and curve[i] > curve[i + 1]]
    return sorted(peaks, reverse=True)[:n]


def fmt_f(f):
    return "%.1f kHz" % (f / 1000) if f >= 1000 else "%d Hz" % round(f)


# ---------------------------------------------------------------- etat & generation
def load_state():
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    return {**DEFAULT_STATE, **st}


def overlay_filters(st, steps):
    fl = []
    if st["mal_db"]:
        fl.append(("PK", MAL["fc"], st["mal_db"], MAL["q"], "/mal 3-7 kHz"))
    if st["plus_db"]:
        for f in steps:
            fl.append(("PK", f, st["plus_db"], PLUS["q"], "/plusdepas"))
    for e in st["extra"]:
        fl.append(("PK", e["fc"], e["gain"], e["q"], e.get("note", "adj")))
    return fl


def headset_chain(st):
    prof = parse_file(PROFILES / st["headset"])
    steps = prof["steps"] or DEFAULT_FOOTSTEPS
    ov = overlay_filters(st, steps)
    filters = prof["filters"] + [f[:4] for f in ov]
    curve = response(filters, prof["xfeeds"])
    total = needed_preamp(curve)
    comp = round(total - sum(prof["preamps"]), 1)
    return {"prof": prof, "overlay": ov, "curve": curve, "total": total, "comp": comp,
            "steps": steps}


def write_all(st):
    hc = headset_chain(st)
    lines = ["# GENERE PAR claude_tools\\eqctl.py - NE PAS EDITER A LA MAIN",
             "# Ajustements live du casque + compensation exacte du Preamp de la chaine.",
             "# Profil casque : %s | /mal %+.1f dB | /plusdepas %+.1f dB" % (
                 st["headset"], st["mal_db"], st["plus_db"]),
             "# Preamp total de la chaine casque : %.1f dB" % hc["total"],
             "Preamp: %.1f dB" % hc["comp"]]
    for k, fc, g, q, note in hc["overlay"]:
        lines.append("# %s" % note)
        lines.append("Filter: ON %s Fc %g Hz Gain %.1f dB Q %.2f" % (k, fc, g, q))
    OVERLAY.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = ["# ============================================================",
         "# claude_active.txt - ROUTEUR GENERE PAR claude_tools\\eqctl.py",
         "# Source : claude_profiles\\_state.json. Ne pas editer a la main :",
         "# utiliser /pas /clutch /chill /solo /enceintes /nuit /reset.",
         "# ============================================================", ""]
    if st.get("bypass"):
        r += ["# >>> BYPASS A/B ACTIF : son d'origine sur les deux sorties <<<", ""]
        routes = (("headset", [NEUTRAL]), ("speakers", [NEUTRAL]))
    else:
        routes = (("headset", [st["headset"], OVERLAY.name]), ("speakers", [st["speakers"]]))
    for dev, inc in routes:
        guid, label = DEVICES[dev]
        if guid == ZERO_GUID:
            r += ["# --- %s : sortie non choisie, ignoree (python eqctl.py devices) ---" % label, ""]
            continue
        r += ["# --- %s ---" % label, "Device: %s" % guid, "Channel: all"]
        r += ["Include: claude_profiles\\%s" % i for i in inc] + [""]
    r += ["# --- Reinitialisation pour la suite de config.txt ---", "Device: all", "Channel: all"]
    ROUTER.write_text("\n".join(r) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    return hc


def status_lines(st, hc=None):
    hc = hc or headset_chain(st)
    peaks = ", ".join("%s %+.1f dB" % (fmt_f(f), g) for g, f in top_peaks(hc["curve"])) or "aucun boost"
    adj = []
    if st["mal_db"]:
        adj.append("/mal %+.1f" % st["mal_db"])
    if st["plus_db"]:
        adj.append("/plusdepas %+.1f" % st["plus_db"])
    if st["extra"]:
        adj.append("%d adj" % len(st["extra"]))
    sp = parse_file(PROFILES / st["speakers"])
    sp_pre = needed_preamp(response(sp["filters"], sp["xfeeds"]))
    by = " | >>> BYPASS A/B ACTIF (son d'origine) <<<" if st.get("bypass") else ""
    return ["Profil actif : casque = %s%s | enceintes = %s%s" % (
                st["headset"], (" [" + ", ".join(adj) + "]") if adj else "", st["speakers"], by),
            "Preamp : casque %.1f dB (chaine complete) | enceintes %.1f dB" % (hc["total"], sp_pre),
            "Top boosts casque : " + peaks]


def fix_preamp_file(p):
    own = parse_file(p, follow=False)
    pre = needed_preamp(response(own["filters"], own["xfeeds"]))
    txt = p.read_text(encoding="utf-8-sig")
    new, n = re.subn(r"(?im)^(\s*Preamp\s*:\s*)-?[\d.,]+(\s*dB)", r"\g<1>%.1f\g<2>" % pre, txt, count=1)
    if n == 0:
        new = "Preamp: %.1f dB\n" % pre + txt
    if new != txt:
        p.write_text(new, encoding="utf-8")
    return pre


def fix_preamps():
    return ["%-30s Preamp propre %.1f dB" % (p.name, fix_preamp_file(p))
            for p in sorted(PROFILES.glob("*.txt")) if not p.name.startswith("_")]


def save_perso(st):
    """Fige profil casque actif + ajustements live dans 07_perso.txt et l'active."""
    import datetime
    src = PROFILES / st["headset"]
    hc = headset_chain(st)
    lines = src.read_text(encoding="utf-8-sig").splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1  # saute l'en-tete (commentaires de tete)
    body = [l for l in lines[i:] if not RE_PREAMP.match(l.split("#", 1)[0])]
    out = ["# " + "=" * 60,
           "# 07_perso.txt - /perso - profil personnel sauvegarde le %s"
           % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "# Base : %s + ajustements live (/mal %+.1f, /plusdepas %+.1f, %d adj)"
           % (st["headset"], st["mal_db"], st["plus_db"], len(st["extra"])),
           "# @footsteps " + " ".join("%g" % f for f in hc["steps"]),
           "# " + "=" * 60, "Preamp: 0.0 dB"] + body
    if hc["overlay"]:
        out += ["", "# --- Ajustements live figes ---", "Channel: all"]
        for k, fc, g, q, note in hc["overlay"]:
            out += ["# " + note, "Filter: ON %s Fc %g Hz Gain %.1f dB Q %.2f" % (k, fc, g, q)]
    dst = PROFILES / PERSO
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    fix_preamp_file(dst)
    st.update(headset=PERSO, mal_db=0.0, plus_db=0.0, extra=[], bypass=False)
    return write_all(st)


# ---------------------------------------------------------------- CLI
def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0].lstrip("/").lower(), argv[1:]
    st = load_state()
    if cmd not in ("bypass", "status", "analyze", "curve"):
        st["bypass"] = False  # toute action reelle sort du mode comparaison A/B
    if cmd in ALIASES:
        dev, prof = ALIASES[cmd]
        if not (PROFILES / prof).exists():
            print("ERREUR : %s n'existe pas encore (utilise 'save' pour le creer)" % prof)
            return 1
        st[dev] = prof
        if dev == "headset":
            st.update(mal_db=0.0, plus_db=0.0, extra=[])
        hc = write_all(st)
        if dev == "headset":
            print("OK /%s -> casque = %s | Preamp chaine %.1f dB | ajustements remis a 0"
                  % (cmd, prof, hc["total"]))
        else:
            sp = parse_file(PROFILES / prof)
            print("OK /%s -> enceintes = %s | Preamp %.1f dB"
                  % (cmd, prof, needed_preamp(response(sp["filters"], sp["xfeeds"]))))
    elif cmd == "mal":
        new = max(MAL["floor"], st["mal_db"] + MAL["step"])
        clamp = " (butee %.0f dB)" % MAL["floor"] if new == st["mal_db"] else ""
        st["mal_db"] = new
        hc = write_all(st)
        print("OK /mal -> 3-7 kHz a %+.1f dB sur %s | Preamp chaine %.1f dB%s"
              % (new, st["headset"], hc["total"], clamp))
    elif cmd == "plusdepas":
        new = min(PLUS["ceil"], st["plus_db"] + PLUS["step"])
        clamp = " (butee +%.0f dB)" % PLUS["ceil"] if new == st["plus_db"] else ""
        st["plus_db"] = new
        hc = write_all(st)
        print("OK /plusdepas -> %s a %+.1f dB sur %s | Preamp chaine %.1f dB%s"
              % (" + ".join(fmt_f(f) for f in hc["steps"]), new, st["headset"], hc["total"], clamp))
    elif cmd == "adj":
        fc, g, q = (float(x) for x in args[:3])
        st["extra"].append({"fc": fc, "gain": g, "q": q, "note": " ".join(args[3:]) or "adj"})
        hc = write_all(st)
        print("OK adj -> PK %s %+.1f dB Q %.2f sur %s | Preamp chaine %.1f dB"
              % (fmt_f(fc), g, q, st["headset"], hc["total"]))
    elif cmd == "undo":
        if not st["extra"]:
            print("Rien a annuler : aucun ajustement adj actif")
            return 0
        e = st["extra"].pop()
        hc = write_all(st)
        print("OK undo -> retire PK %s %+.1f dB (%s) | Preamp chaine %.1f dB"
              % (fmt_f(e["fc"]), e["gain"], e.get("note", "adj"), hc["total"]))
    elif cmd == "clearadj":
        st.update(mal_db=0.0, plus_db=0.0, extra=[])
        hc = write_all(st)
        print("OK clearadj -> ajustements live effaces sur %s | Preamp chaine %.1f dB"
              % (st["headset"], hc["total"]))
    elif cmd == "bypass":
        mode = (args[0].lower() if args else "toggle")
        st["bypass"] = (not st.get("bypass")) if mode == "toggle" else (mode == "on")
        write_all(st)
        print("OK bypass -> %s" % ("ON : son d'origine (comparaison A/B)" if st["bypass"]
                                   else "OFF : profils restaures"))
    elif cmd == "save":
        hc = save_perso(st)
        print("OK save -> profil fige dans %s et active | Preamp chaine %.1f dB"
              % (PERSO, hc["total"]))
    elif cmd == "reset":
        st = dict(DEFAULT_STATE, extra=[])
        write_all(st)
        print("OK /reset -> casque et enceintes en neutre (bypass), ajustements effaces")
    elif cmd == "devices":
        cur = {g: r for r, (g, _n) in DEVICES.items()}
        for i, (g, n) in enumerate(list_render_devices(), 1):
            print("%2d  %s  %s%s" % (i, g, n, ("   <- " + cur[g]) if g in cur else ""))
        print("Choisir : python eqctl.py setdevice headset|speakers NUMERO")
    elif cmd == "setdevice":
        role = (args[0].lower() if args else "")
        devs = list_render_devices()
        if role not in ("headset", "speakers") or len(args) < 2 or not args[1].isdigit() \
                or not 1 <= int(args[1]) <= len(devs):
            print("Usage : python eqctl.py setdevice headset|speakers NUMERO  (numeros : python eqctl.py devices)")
            return 1
        g, n = devs[int(args[1]) - 1]
        set_device(role, g, ("CASQUE : " if role == "headset" else "ENCEINTES : ") + n)
        write_all(st)
        print("OK %s -> %s" % (role, n))
    elif cmd == "status":
        print("\n".join(status_lines(st)))
    elif cmd == "fixpreamp":
        print("\n".join(fix_preamps()))
        write_all(st)
    elif cmd == "analyze":
        d = parse_file(args[0])
        c = response(d["filters"], d["xfeeds"])
        print("%s : %d filtres, Preamps %s, pic %+.2f dB -> Preamp requis %.1f dB"
              % (args[0], len(d["filters"]), d["preamps"], max(c), needed_preamp(c)))
        print("Top : " + ", ".join("%s %+.1f" % (fmt_f(f), g) for g, f in top_peaks(c, 5)))
    elif cmd == "curve":
        hc = headset_chain(st)
        marks = [30, 60, 85, 120, 200, 320, 500, 1000, 2000, 2400, 3000, 3800, 4300, 5000,
                 6500, 7500, 10000, 15000]
        net = hc["total"]
        for f in marks:
            i = min(range(len(GRID)), key=lambda j: abs(GRID[j] - f))
            print("%9s  EQ %+6.1f dB   net(apres Preamp) %+6.1f dB"
                  % (fmt_f(f), hc["curve"][i], hc["curve"][i] + net))
    else:
        print("Commande inconnue : " + cmd)
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
