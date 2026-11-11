"""Темы в стиле VS Code. Палитра и акцент берутся из настроек и меняются на лету.

Интерфейс собран из «карточек» (боковая панель, область вкладок, нижняя панель) со скруглением
и небольшими промежутками между ними; фон окна (base) виден в этих промежутках.
"""
from pathlib import Path

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication

from .. import settings
from .icons import svg_file

ACCENTS = {
    "blue": ("Синий", "#0078d4"),
    "purple": ("Фиолетовый", "#8957e5"),
    "green": ("Зелёный", "#238636"),
    "orange": ("Оранжевый", "#c96b0c"),
    "pink": ("Розовый", "#bf3989"),
}

_DARK_COMMON = {"green": "#3fb950", "red": "#f85149", "scroll": "rgba(121,121,121,0.4)",
                "scroll_hover": "rgba(100,100,100,0.7)", "select_text": "#ffffff"}
_LIGHT_COMMON = {"green": "#1a7f37", "red": "#cf222e", "scroll": "rgba(100,100,100,0.35)",
                 "scroll_hover": "rgba(100,100,100,0.6)"}

THEMES = {
    "dark": ("Тёмная", {**_DARK_COMMON,
        "base": "#121212", "sidebar": "#181818", "editor": "#1f1f1f", "panel": "#181818",
        "card_border": "#2b2b2b", "border": "#2b2b2b", "row_line": "#252525", "tab_sel": "#2a2a2a",
        "text": "#cccccc", "bright": "#e7e7e7", "muted": "#9d9d9d", "faint": "#6e7681",
        "input": "#313131", "input_border": "#3c3c3c", "hover": "#2a2d2e", "select": "#04395e",
        "toolhover": "#2f3133", "menu": "#1f1f1f", "menu_border": "#454545", "modal": "#202020",
        "cmd_bg": "#1f1f1f", "cmd_hover": "#252526", "scrim": 150}),
    "oled_dark": ("OLED тёмная", {**_DARK_COMMON,
        "base": "#000000", "sidebar": "#000000", "editor": "#000000", "panel": "#000000",
        "card_border": "#262626", "border": "#1c1c1c", "row_line": "#111111", "tab_sel": "#141414",
        "text": "#d4d4d4", "bright": "#ffffff", "muted": "#8b8b8b", "faint": "#5c5c5c",
        "input": "#0e0e0e", "input_border": "#2e2e2e", "hover": "#121212", "select": "#0b2e4f",
        "toolhover": "#1a1a1a", "menu": "#0a0a0a", "menu_border": "#333333", "modal": "#0a0a0a",
        "cmd_bg": "#0a0a0a", "cmd_hover": "#121212", "scrim": 170}),
    "light": ("Светлая", {**_LIGHT_COMMON,
        "base": "#e8e8e8", "sidebar": "#f8f8f8", "editor": "#ffffff", "panel": "#f8f8f8",
        "card_border": "#dadada", "border": "#e5e5e5", "row_line": "#f0f0f0", "tab_sel": "#ececec",
        "text": "#3b3b3b", "bright": "#1f1f1f", "muted": "#6e6e6e", "faint": "#8b8b8b",
        "input": "#ffffff", "input_border": "#cecece", "hover": "#f0f0f0", "select": "#d8eaf9",
        "select_text": "#1f1f1f", "toolhover": "#e3e3e3", "menu": "#ffffff", "menu_border": "#d4d4d4",
        "modal": "#ffffff", "cmd_bg": "#ffffff", "cmd_hover": "#f5f5f5", "scrim": 90}),
    "oled_light": ("OLED светлая", {**_LIGHT_COMMON,
        "base": "#ffffff", "sidebar": "#ffffff", "editor": "#ffffff", "panel": "#ffffff",
        "card_border": "#cfcfcf", "border": "#e2e2e2", "row_line": "#f2f2f2", "tab_sel": "#f0f0f0",
        "text": "#111111", "bright": "#000000", "muted": "#4d4d4d", "faint": "#757575",
        "input": "#ffffff", "input_border": "#bdbdbd", "hover": "#f2f2f2", "select": "#dbe9fb",
        "select_text": "#000000", "toolhover": "#ebebeb", "menu": "#ffffff", "menu_border": "#c8c8c8",
        "modal": "#ffffff", "cmd_bg": "#ffffff", "cmd_hover": "#f7f7f7", "scrim": 80}),
}

C: dict = {}
_ASSETS = Path(__file__).parent / "assets"


def is_light() -> bool:
    return settings.get("theme") in ("light", "oled_light")


def _sync():
    theme = settings.get("theme")
    C.clear()
    C.update(THEMES.get(theme, THEMES["dark"])[1])
    C["accent"] = ACCENTS.get(settings.get("accent"), ACCENTS["blue"])[1]
    C["accent_hover"] = QColor(C["accent"]).darker(112).name()


_sync()


def _qss() -> str:
    _ASSETS.mkdir(exist_ok=True)
    t = settings.get("theme")
    # имя файла зависит от темы: Qt кэширует картинки по пути
    chevron = svg_file("chevron", C["muted"], _ASSETS / f"chevron_{t}.svg")
    chevron_up = svg_file("chevron_up", C["muted"], _ASSETS / f"chevron_up_{t}.svg")
    check = svg_file("check", "#ffffff", _ASSETS / "check.svg")
    c = C
    return f"""
* {{ color: {c['text']}; font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 13px; }}
QMainWindow, QWidget#central {{ background: {c['base']}; }}
QLabel {{ background: transparent; }}

/* ---------- карточки ---------- */
QFrame#sideCard {{ background: {c['sidebar']}; border: 1px solid {c['card_border']}; border-radius: 8px; }}
QFrame#editorCard {{ background: {c['editor']}; border: 1px solid {c['card_border']}; border-radius: 8px; }}
QFrame#panelCard {{ background: {c['panel']}; border: 1px solid {c['card_border']}; border-radius: 8px; }}
QStackedWidget#sideStack, QFrame#sideBar, QStackedWidget#pages, QWidget#page, QStackedWidget#tabActions {{ background: transparent; }}
QSplitter {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}

/* ---------- командный центр ---------- */
QFrame#titleBar {{ background: {c['base']}; border: none; }}
QLabel#appName {{ color: {c['muted']}; font-size: 12px; padding-left: 14px; }}
QLineEdit#commandCenter {{ background: {c['cmd_bg']}; border: 1px solid {c['card_border']}; border-radius: 6px;
    padding: 3px 10px; color: {c['text']}; font-size: 12px; }}
QLineEdit#commandCenter:hover {{ border-color: {c['input_border']}; background: {c['cmd_hover']}; }}
QLineEdit#commandCenter:focus {{ border-color: {c['accent']}; }}

/* ---------- activity bar ---------- */
QFrame#activityBar {{ background: {c['base']}; border: none; }}
QToolButton#activityBtn {{ background: transparent; border: none; border-radius: 0;
    border-left: 2px solid transparent; border-right: 2px solid transparent; padding: 0; margin: 0; }}
QToolButton#activityBtn:hover {{ background: transparent; }}
QToolButton#activityBtn:checked {{ border-left: 2px solid {c['accent']}; }}

/* ---------- sidebar ---------- */
QLabel#sideTitle {{ color: {c['muted']}; font-size: 11px; letter-spacing: 0.5px; padding: 0 0 0 18px; }}
QLabel#sectionTitle {{ color: {c['text']}; font-size: 11px; font-weight: 700; padding: 0; }}
QFrame#sectionHeader {{ background: transparent; border: none; border-top: 1px solid {c['border']}; }}
QListWidget {{ background: transparent; border: none; outline: none; padding: 0 4px; }}
QListWidget::item {{ padding: 3px 8px 3px 14px; border: 1px solid transparent; border-radius: 4px; color: {c['text']}; }}
QListWidget::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected {{ background: {c['select']}; border: 1px solid {c['accent']}; color: {c['select_text']}; }}
QListWidget::indicator {{ width: 14px; height: 14px; border: 1px solid {c['input_border']}; border-radius: 3px; background: {c['input']}; }}
QListWidget::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QListWidget#boxList {{ background: {c['input']}; border: 1px solid {c['input_border']}; border-radius: 4px; padding: 4px; }}
QListWidget#boxList::item {{ padding: 4px 6px; }}

/* ---------- вкладки ---------- */
QFrame#tabRow {{ background: transparent; border: none; border-bottom: 1px solid {c['border']}; }}
QWidget#tabStrip {{ background: transparent; }}
QFrame#editorTab {{ background: transparent; border: none; border-bottom: 2px solid transparent;
    border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QFrame#editorTab:hover {{ background: {c['hover']}; }}
QFrame#editorTab[selected="true"] {{ background: {c['tab_sel']}; border-bottom: 2px solid {c['accent']}; }}
QFrame#editorTab[dragging="true"] {{ background: {c['tab_sel']}; border: 1px solid {c['card_border']};
    border-bottom: 2px solid {c['accent']}; }}
QLabel#tabTitle {{ color: {c['muted']}; }}
QFrame#editorTab[selected="true"] QLabel#tabTitle {{ color: {c['bright']}; }}
QToolButton#tabClose {{ border-radius: 4px; padding: 2px; }}
QToolButton#tabClose:hover {{ background: {c['toolhover']}; }}
QLabel#crumbs {{ color: {c['muted']}; font-size: 12px; }}

/* ---------- кнопки ---------- */
QPushButton {{ background: {c['input']}; color: {c['bright']}; border: 1px solid {c['input_border']};
    border-radius: 4px; padding: 4px 12px; min-height: 18px; }}
QPushButton:hover {{ background: {c['toolhover']}; }}
QPushButton:pressed {{ background: {c['select']}; }}
QPushButton#primary {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: #ffffff; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#dangerBtn {{ background: #c42b2b; border: 1px solid #c42b2b; color: #ffffff; }}
QPushButton#dangerBtn:hover {{ background: #d13c3c; }}
QPushButton:disabled {{ color: {c['faint']}; }}
QToolButton {{ background: transparent; border: none; border-radius: 5px; padding: 3px; }}
QToolButton:hover {{ background: {c['toolhover']}; }}

/* ---------- поля ---------- */
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit {{ background: {c['input']}; border: 1px solid {c['input_border']};
    border-radius: 4px; padding: 4px 8px; selection-background-color: {c['select']}; selection-color: {c['select_text']};
    color: {c['text']}; min-height: 18px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {c['menu']}; border: 1px solid {c['menu_border']}; padding: 2px;
    selection-background-color: {c['accent']}; selection-color: #ffffff; outline: none; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {c['faint']}; background: {c['hover']}; }}
QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{ subcontrol-origin: border; width: 18px; border: none; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow, QDateTimeEdit::up-arrow {{ image: url({chevron_up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow, QDateTimeEdit::down-arrow {{ image: url({chevron}); width: 10px; height: 10px; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c['input_border']}; border-radius: 3px; background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}

/* ---------- таблицы ---------- */
QTableWidget {{ background: transparent; border: none; gridline-color: transparent; outline: none;
    selection-background-color: {c['select']}; selection-color: {c['select_text']}; }}
QTableWidget::item {{ padding: 0 8px; border: none; border-bottom: 1px solid {c['row_line']}; }}
QTableWidget::item:hover {{ background: {c['hover']}; }}
QTableWidget::item:selected {{ background: {c['select']}; color: {c['select_text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 6px 8px; font-size: 11px; font-weight: 600; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QLabel#emptyState {{ color: {c['faint']}; font-size: 13px; }}

/* ---------- нижняя панель ---------- */
QTabWidget#bottomPanel {{ background: transparent; }}
QTabWidget#bottomPanel::pane {{ border: none; background: transparent; }}
QTabWidget#bottomPanel QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 2px 6px 2px;
    margin: 0 8px; border: none; border-bottom: 1px solid transparent; font-size: 11px; }}
QTabWidget#bottomPanel QTabBar::tab:hover {{ color: {c['bright']}; }}
QTabWidget#bottomPanel QTabBar::tab:selected {{ color: {c['bright']}; border-bottom: 1px solid {c['accent']}; }}
QPlainTextEdit#console {{ background: transparent; border: none; border-radius: 0; padding: 4px 10px;
    font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; color: {c['text']}; }}

/* ---------- встроенный браузер ---------- */
QWidget#browserTab {{ background: {c['editor']}; }}
QLabel#browserHint {{ color: {c['faint']}; font-size: 12px; }}

/* ---------- настройки ---------- */
QScrollArea#settingsScroll, QWidget#settingsBody {{ background: transparent; border: none; }}
QLabel#settingsH1 {{ color: {c['bright']}; font-size: 22px; font-weight: 600; }}
QLabel#settingsH2 {{ color: {c['bright']}; font-size: 15px; font-weight: 600; }}
QFrame#settingRow {{ background: transparent; border-radius: 6px; }}
QFrame#settingRow:hover {{ background: {c['hover']}; }}
QLabel#settingTitle {{ color: {c['bright']}; font-weight: 600; }}
QLabel#settingCat {{ color: {c['muted']}; }}
QLabel#settingDesc {{ color: {c['muted']}; font-size: 12px; }}

/* ---------- модальные окна ---------- */
QFrame#modalCard {{ background: {c['modal']}; border: 1px solid {c['input_border']}; border-radius: 10px; }}
QFrame#modalHead {{ background: transparent; border: none; border-bottom: 1px solid {c['border']}; }}
QFrame#modalFoot {{ background: transparent; border: none; border-top: 1px solid {c['border']}; }}
QLabel#modalTitle {{ color: {c['bright']}; font-size: 14px; font-weight: 600; }}
QLabel#modalText {{ color: {c['text']}; }}
QLabel#modalError {{ color: {c['red']}; }}
QLabel#formLabel {{ color: {c['muted']}; }}
QLabel#hint {{ color: {c['faint']}; font-size: 12px; }}

/* ---------- прокрутка ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; min-height: 30px; border-radius: 4px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{ background: {c['scroll']}; min-width: 30px; border-radius: 4px; margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

/* ---------- строка состояния ---------- */
QStatusBar {{ background: {c['base']}; border: none; min-height: 24px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {c['muted']}; font-size: 12px; padding: 0 8px; }}
QLabel#remote {{ background: {c['accent']}; color: #ffffff; padding: 0 10px; border-radius: 4px; margin: 2px 0 2px 6px; }}

/* ---------- меню и подсказки ---------- */
QMenu {{ background: {c['menu']}; border: 1px solid {c['menu_border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 28px 5px 10px; border-radius: 4px; background: transparent; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['accent']}; color: #ffffff; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {c['menu_border']}; margin: 4px 6px; }}
QToolTip {{ background: {c['menu']}; color: {c['text']}; border: 1px solid {c['menu_border']}; padding: 4px 6px; }}
"""


def apply_theme(app: QApplication):
    _sync()
    app.setStyle("Fusion")
    f = QFont("Segoe UI", 10)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    app.setStyleSheet(_qss())
