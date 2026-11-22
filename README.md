# Arma Audio HUD

Petit panneau de contrôle audio pour **Equalizer APO** (Windows) : 10 profils sonores prêts à l'emploi
(radar de pas, chirurgien, confort longue session, cinéma, musique…), un HUD en surimpression et un outil en ligne de commande.
Pensé pour Arma Reforger, mais ça marche pour n'importe quel jeu, film ou musique.

> **Bêta.** Le HUD a été reconstruit à partir de notes et n'a pas été éprouvé longtemps. Rien n'est supprimé à l'installation,
> et tout se retire avec `python setup.py --uninstall`.

## Ce qu'il te faut
- Windows 10/11
- [Equalizer APO](https://sourceforge.net/projects/equalizerapo/) installé (avec ton casque / tes enceintes cochés dans son Configurator)
- [Python 3.10+](https://www.python.org/downloads/) (avec tkinter, inclus par défaut)

## Installation (2 minutes)
1. Télécharge ce dépôt (bouton vert **Code > Download ZIP**) et décompresse-le.
2. Ouvre **PowerShell en administrateur** (Equalizer APO est dans Program Files) dans le dossier.
3. Teste d'abord sans rien changer :
   ```powershell
   python setup.py --dry-run
   ```
4. Puis installe :
   ```powershell
   python setup.py
   ```
   Le script copie les fichiers (sans écraser les tiens), sauvegarde `config.txt` en `config.txt.bak-AAAAMMJJ`,
   ajoute la ligne `Include: claude_active.txt`, puis te demande quelle sortie audio est ton **casque** et quelle est ta paire d'**enceintes**.
5. Lance le HUD : `python "<dossier config>\claude_tools\arma_audio_hud.pyw"` (le chemin est affiché à la fin).

> ⚠️ **Commence avec le volume bas.** Certains profils amplifient les sons faibles : monte le volume doucement.

## Choisir ou changer ses sorties audio
(Les commandes `eqctl.py` se lancent depuis le dossier `claude_tools` installé dans le dossier `config` d'Equalizer APO.)
```powershell
python eqctl.py devices            # liste tes sorties numérotées
python eqctl.py setdevice headset 2
python eqctl.py setdevice speakers 3
```

## Commandes utiles
```powershell
python eqctl.py status             # profil actif
python eqctl.py reset              # retour au son neutre
python eqctl.py fixpreamp          # recalcule les Preamp (anti-saturation)
```

## Corriger la réponse de ton casque
`claude_profiles\base_headphone_fix.txt` est neutre par défaut. Le dossier `claude_profiles\corrections\` contient des
corrections toutes faites (ex. `hyperx_cloud_ii.txt`, issue d'AutoEq). Copie la tienne par-dessus `base_headphone_fix.txt`,
puis lance `python eqctl.py fixpreamp`. Tu peux ajouter ton modèle depuis [AutoEq](https://github.com/jaakkopasanen/AutoEq).

## Désinstaller
```powershell
python setup.py --uninstall
```
Retire la ligne Include et les dossiers copiés. Ta sauvegarde `config.txt.bak-*` est conservée.

## Licence
MIT, voir `LICENSE`. Fourni tel quel, sans garantie.
