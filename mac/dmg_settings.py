# dmgbuild settings for the Job Oyster DMG: a fixed window with the app on the left, Applications on the
# right and mac/make_dmg_background.swift's picture behind them. mac/build.sh passes the paths with -D.
# dmgbuild writes the window's layout itself, so no Finder or AppleScript is needed (GitHub's Macs have neither).
# No hide_extensions: it sets Finder info on the app, which breaks its signature (Finder hides ".app" anyway).
import os.path

app = defines["app"]  # noqa: F821 (dmgbuild provides defines)
files = [app]
symlinks = {"Applications": "/Applications"}
icon = defines["icon"]  # noqa: F821
background = defines["background"]  # noqa: F821

format = "UDZO"
filesystem = "HFS+"

window_rect = ((200, 160), (660, 430))  # with the title bar; the background is 660x440
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
arrange_by = None
icon_size = 112
text_size = 13
icon_locations = {os.path.basename(app): (170, 165), "Applications": (490, 165)}
