"""The editor's colours, chosen in Tools > Settings > Theme (kept in editor.ini): System (light or dark as the operating
system is), Light or Dark (colours like those of JetBrains' dark theme). Taskloom's own colours are shown in Qt's Fusion
style, which can be dark on every system (Windows 10's style cannot); light on a light system is Qt's own look, as
without a theme."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QToolTip

THEMES = ("System", "Light", "Dark")
# IntelliJ's dark theme: tool windows, editor and text areas, text, hints, selection, borders, links.
DARK = {QPalette.Window: "#2b2d30", QPalette.WindowText: "#dfe1e5", QPalette.Base: "#1e1f22",
        QPalette.AlternateBase: "#26282b", QPalette.Text: "#dfe1e5", QPalette.PlaceholderText: "#868a91",
        QPalette.Button: "#2b2d30", QPalette.ButtonText: "#dfe1e5", QPalette.BrightText: "#ffffff",
        QPalette.ToolTipBase: "#2b2d30", QPalette.ToolTipText: "#dfe1e5", QPalette.Highlight: "#2e436e",
        QPalette.HighlightedText: "#dfe1e5", QPalette.Accent: "#3574f0", QPalette.Link: "#589df6",
        QPalette.LinkVisited: "#589df6", QPalette.Light: "#4e5157", QPalette.Midlight: "#43454a",
        QPalette.Mid: "#393b40", QPalette.Dark: "#1e1f22", QPalette.Shadow: "#151618"}
DARK_DISABLED = {QPalette.WindowText: "#6f737a", QPalette.Text: "#6f737a", QPalette.ButtonText: "#6f737a",
                 QPalette.PlaceholderText: "#6f737a"}
# Fusion's own light colours, for Light on a dark system (Fusion's standardPalette() is dark when the system is).
LIGHT = {QPalette.Window: "#efefef", QPalette.WindowText: "#000000", QPalette.Base: "#ffffff",
         QPalette.AlternateBase: "#f7f7f7", QPalette.Text: "#000000", QPalette.PlaceholderText: "#80000000",
         QPalette.Button: "#efefef", QPalette.ButtonText: "#000000", QPalette.BrightText: "#ffffff",
         QPalette.ToolTipBase: "#ffffdc", QPalette.ToolTipText: "#000000", QPalette.Highlight: "#308cc6",
         QPalette.HighlightedText: "#ffffff", QPalette.Accent: "#308cc6", QPalette.Link: "#0000ff",
         QPalette.LinkVisited: "#ff00ff", QPalette.Light: "#ffffff", QPalette.Midlight: "#cacaca",
         QPalette.Mid: "#b8b8b8", QPalette.Dark: "#9f9f9f", QPalette.Shadow: "#767676"}
LIGHT_DISABLED = {QPalette.WindowText: "#bebebe", QPalette.Text: "#bebebe", QPalette.ButtonText: "#bebebe",
                  QPalette.Base: "#efefef", QPalette.Dark: "#bebebe", QPalette.Shadow: "#b1b1b1",
                  QPalette.Highlight: "#919191", QPalette.Accent: "#919191"}

_qt_style = None  # Qt's own style, as it was when Taskloom's colours were shown instead


def chosen(settings) -> str:
    """The theme chosen in Settings, kept in the editor's preferences (`settings`, editor.ini); System if none was."""
    return settings.value("theme", "System")


def system_is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply(theme: str):
    """Show `theme` in the whole application: its windows, dialogs, menus and tooltips, and what reads the colours when
    it draws (System: as the operating system is now)."""
    global _qt_style
    app = QApplication.instance()
    shown = app.testAttribute(Qt.AA_SetPalette)  # Taskloom's colours are shown (nothing else sets the application's)
    dark = theme == "Dark" or theme == "System" and system_is_dark()
    if dark or system_is_dark():  # Taskloom's dark or (Light on a dark system) Fusion's light
        if not shown:
            _qt_style = app.style().name()
        if app.style().name().lower() != "fusion":
            app.setStyle("Fusion")
        colours, disabled = (DARK, DARK_DISABLED) if dark else (LIGHT, LIGHT_DISABLED)
        palette = QPalette()
        for role, value in colours.items():
            palette.setColor(role, QColor(value))
        for role, value in disabled.items():
            palette.setColor(QPalette.Disabled, role, QColor(value))
        app.setPalette(palette)  # (Qt then drops the system's own colours for menus and the like)
    elif shown:  # light on a light system: Qt's own look again
        app.setPalette(QPalette())  # the system's colours
        if _qt_style and app.style().name().lower() != _qt_style.lower():
            app.setStyle(_qt_style)
    else:
        return  # Qt's own look already
    QToolTip.setPalette(app.palette())
    for widget in app.allWidgets():  # a style sheet's colours (e.g. palette(placeholder-text)) are read again
        if widget.styleSheet():
            widget.setStyleSheet(widget.styleSheet())
