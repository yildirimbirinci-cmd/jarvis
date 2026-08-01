from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_with_fake_qt():
    qtcore = types.ModuleType("PySide6.QtCore")
    qtgui = types.ModuleType("PySide6.QtGui")
    qtwidgets = types.ModuleType("PySide6.QtWidgets")

    class DummySignal:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, callback):
            self.callback = callback

        def emit(self, *args, **kwargs):
            callback = getattr(self, "callback", None)
            if callback:
                callback(*args, **kwargs)

    class Dummy:
        def __init__(self, *args, **kwargs):
            pass

    class DummyWidget(Dummy):
        pass

    qtcore.Qt = types.SimpleNamespace(MatchExactly=0)
    qtcore.QTimer = Dummy
    qtcore.Signal = DummySignal
    qtgui.QFont = Dummy
    for name in (
        "QAbstractItemView",
        "QHBoxLayout",
        "QLabel",
        "QMessageBox",
        "QPlainTextEdit",
        "QProgressBar",
        "QPushButton",
        "QTreeWidget",
        "QTreeWidgetItem",
        "QVBoxLayout",
    ):
        setattr(qtwidgets, name, Dummy)
    qtwidgets.QWidget = DummyWidget
    package = types.ModuleType("PySide6")
    package.QtCore = qtcore
    package.QtGui = qtgui
    package.QtWidgets = qtwidgets
    sys.modules["PySide6"] = package
    sys.modules["PySide6.QtCore"] = qtcore
    sys.modules["PySide6.QtGui"] = qtgui
    sys.modules["PySide6.QtWidgets"] = qtwidgets

    path = Path(__file__).resolve().parents[1] / "core" / "project_development_ui.py"
    spec = importlib.util.spec_from_file_location("project_development_ui_fakeqt", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_installer_wraps_main_window_once(monkeypatch) -> None:
    module = _load_with_fake_qt()

    class FakePanel:
        def __init__(self, window):
            self.window = window

    monkeypatch.setattr(module, "ProjectDevelopmentPanel", FakePanel)

    class Tabs:
        def __init__(self):
            self.rows = []

        def addTab(self, widget, label):
            self.rows.append((widget, label))

    class MainWindow:
        def __init__(self):
            self.tabs = Tabs()
            self.engine = object()

    module.install_main_window_project_development(MainWindow)
    module.install_main_window_project_development(MainWindow)
    window = MainWindow()
    assert len(window.tabs.rows) == 1
    assert window.tabs.rows[0][1] == "Proje Geliştirme"
    assert window.project_development_panel is window.tabs.rows[0][0]
