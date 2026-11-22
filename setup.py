#!/usr/bin/env python3
"""Installation guidee de Arma Audio HUD pour Equalizer APO.

Ce que fait ce script (rien n'est supprime, config.txt est sauvegarde avant d'etre modifie) :
  1. trouve le dossier `config` d'Equalizer APO ;
  2. y copie `claude_tools` et `claude_profiles` (sans ecraser tes fichiers existants) ;
  3. ajoute `Include: claude_active.txt` a la fin de `config.txt` (copie de securite `config.txt.bak-AAAAMMJJ`) ;
  4. te laisse choisir ta sortie casque et ta sortie enceintes ;
  5. recalcule les Preamp (pour eviter la saturation) et affiche l'etat.

Usage :
  python setup.py                 installation normale (PowerShell en administrateur si Equalizer APO est dans Program Files)
  python setup.py --dry-run       montre ce qui serait fait, ne change rien
  python setup.py --target DOSSIER  installe dans un autre dossier `config` (tests)
  python setup.py --uninstall     retire la ligne Include et les dossiers copies (apres confirmation)
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INCLUDE = "Include: claude_active.txt"
FOLDERS = ("claude_tools", "claude_profiles")


def find_config():
    """Dossier `config` d'Equalizer APO (registre, puis emplacements habituels)."""
    try:
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EqualizerAPO", 0,
                                    winreg.KEY_READ | view) as k:
                    p = Path(winreg.QueryValueEx(k, "InstallPath")[0]) / "config"
                    if p.is_dir():
                        return p
            except OSError:
                continue
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        p = Path(base) / "EqualizerAPO" / "config"
        if p.is_dir():
            return p
    return None


def copy_tree(src, dst, dry):
    """Copie sans ecraser : un fichier deja present chez toi est conserve."""
    n_new = n_kept = 0
    for f in sorted(src.rglob("*")):
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        t = dst / f.relative_to(src)
        if t.exists():
            n_kept += 1
            continue
        n_new += 1
        if not dry:
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
    return n_new, n_kept


def ensure_include(config_txt, dry):
    text = config_txt.read_text(encoding="utf-8-sig") if config_txt.exists() else ""
    if any(l.strip().lower() == INCLUDE.lower() for l in text.splitlines()):
        return "deja present"
    if dry:
        return "serait ajoute"
    if config_txt.exists():
        bak = config_txt.with_name("config.txt.bak-" + datetime.date.today().strftime("%Y%m%d"))
        if not bak.exists():
            shutil.copy2(config_txt, bak)
    with open(config_txt, "a", encoding="utf-8") as fh:
        fh.write(("\n" if text and not text.endswith("\n") else "") + "\n# Arma Audio HUD\n" + INCLUDE + "\n")
    return "ajoute"


def ask_device(role_label, role, eq, devs):
    print("\n--- %s ---" % role_label)
    for i, (g, n) in enumerate(devs, 1):
        print("  %2d  %s" % (i, n))
    while True:
        a = input("Numero de ta sortie %s (Entree = passer) : " % role_label.lower()).strip()
        if not a:
            print("  ignore : tu pourras le faire plus tard avec  python eqctl.py setdevice %s NUMERO" % role)
            return
        if a.isdigit() and 1 <= int(a) <= len(devs):
            g, n = devs[int(a) - 1]
            eq.set_device(role, g, ("CASQUE : " if role == "headset" else "ENCEINTES : ") + n)
            print("  OK : " + n)
            return
        print("  numero invalide")


def main():
    ap = argparse.ArgumentParser(description="Installation guidee de Arma Audio HUD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", help="dossier `config` a utiliser a la place de celui d'Equalizer APO")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--yes", action="store_true", help="ne pose aucune question (sorties audio a choisir plus tard)")
    a = ap.parse_args()

    cfg = Path(a.target).resolve() if a.target else find_config()
    if not cfg or not cfg.is_dir():
        print("Equalizer APO est introuvable. Installe-le d'abord : https://sourceforge.net/projects/equalizerapo/")
        return 1
    print("Dossier de configuration : %s" % cfg)
    config_txt = cfg / "config.txt"

    if a.uninstall:
        if input("Retirer la ligne Include et supprimer claude_tools / claude_profiles ? (oui/non) ").strip().lower() != "oui":
            print("Annule.")
            return 0
        if config_txt.exists():
            lines = config_txt.read_text(encoding="utf-8-sig").splitlines()
            keep = [l for l in lines if l.strip().lower() != INCLUDE.lower() and l.strip() != "# Arma Audio HUD"]
            config_txt.write_text("\n".join(keep) + "\n", encoding="utf-8")
        for f in FOLDERS + ("claude_active.txt",):
            p = cfg / f
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        print("Desinstalle. (config.txt.bak-* est conserve)")
        return 0

    try:
        for f in FOLDERS:
            new, kept = copy_tree(HERE / f, cfg / f, a.dry_run)
            print("%-16s %d fichier(s) %s, %d deja present(s) conserve(s)"
                  % (f, new, "a copier" if a.dry_run else "copie(s)", kept))
        print("config.txt       ligne Include : " + ensure_include(config_txt, a.dry_run))
    except PermissionError as e:
        print("\nAcces refuse : %s\nRelance ce script dans un PowerShell ouvert en ADMINISTRATEUR "
              "(clic droit > Executer en tant qu'administrateur)." % e)
        return 1
    if a.dry_run:
        print("\n(dry-run : rien n'a ete modifie)")
        return 0

    sys.path.insert(0, str(cfg / "claude_tools"))
    import eqctl as eq
    if not a.yes:
        try:
            devs = eq.list_render_devices()
        except OSError:
            devs = []
        if devs:
            ask_device("CASQUE", "headset", eq, devs)
            ask_device("ENCEINTES", "speakers", eq, devs)
        else:
            print("Aucune sortie audio detectee : voir  python eqctl.py devices")
    py = sys.executable
    for args in (["fixpreamp"], ["reset"], ["status"]):
        subprocess.run([py, str(cfg / "claude_tools" / "eqctl.py")] + args, check=False)
    print("\nTermine. Lance le HUD :  python \"%s\"" % (cfg / "claude_tools" / "arma_audio_hud.pyw"))
    print("Premier pas conseille : volume bas, puis essaie le profil RADAR (F1) ou CINEMA (F4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
