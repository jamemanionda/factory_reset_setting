import io
import re
import subprocess
import zipfile
import os
import sys
import struct
import html
import json
import logging
import traceback
import tempfile
import shutil
from datetime import datetime, timedelta

# Increase recursion limit to prevent RecursionError in deep search
sys.setrecursionlimit(10000)

# Filter PyQt5 warning messages (ignore QTableWidget sorting-related warnings)
os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.qobject.connect.warning=false;qt.qobject.connect=false'

import pandas as pd
from pyaxmlparser import APK
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QRadioButton, 
                             QButtonGroup, QCheckBox, QTextEdit, QFileDialog,
                             QGroupBox, QLineEdit, QMessageBox, QProgressBar,
                             QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
                             QDialog, QInputDialog, QTreeWidget, QTreeWidgetItem, QListWidget, QCompleter,
                             QSplitter, QComboBox, QSizePolicy, QMenu)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, qInstallMessageHandler, QTimer
from PyQt5.QtGui import QTextDocument, QTextCursor, QClipboard, QFont, QColor

# sqlite3 is lazy imported (prevent DLL issues)


class CopyableMessageBox(QDialog):
    """Message box with copyable text"""
    def __init__(self, parent, title, message, icon_type="information"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Message text (selectable)
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(message)
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumHeight(300)
        layout.addWidget(self.text_edit)
        
        # Button area
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # Copy button
        btn_copy = QPushButton("Copy")
        btn_copy.clicked.connect(self.copy_text)
        button_layout.addWidget(btn_copy)
        
        # OK button
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_ok.setDefault(True)
        button_layout.addWidget(btn_ok)
        
        layout.addLayout(button_layout)
    
    def copy_text(self):
        """Copy text to clipboard"""
        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.text_edit.toPlainText())
            # Simple notification only (prevent infinite loop)
            self.text_edit.setPlaceholderText("Text copied to clipboard.")
        except Exception as e:
            pass  # Silently handle copy failure


class WorkerThread(QThread):
    """Thread for background tasks"""
    finished = pyqtSignal()
    output = pyqtSignal(str)
    
    def __init__(self, reset_instance):
        super().__init__()
        self.reset_instance = reset_instance
    
    def run(self):
        try:
            self.reset_instance.run_analysis()
        except Exception as e:
            self.output.emit(f"Error occurred: {str(e)}\n")
        finally:
            self.finished.emit()


class DeepSearchThread(QThread):
    """Thread for deep search"""
    finished = pyqtSignal()
    result_found = pyqtSignal(str, str, str, str)  # search_time_str, file_path, match_format, match_value
    progress_updated = pyqtSignal(int, int)  # current, total
    
    def __init__(self, reset_instance, search_times, gui_instance, time_tolerance_seconds=300):
        super().__init__()
        self.reset_instance = reset_instance
        self.search_times = search_times
        self.gui_instance = gui_instance
        self.time_tolerance_seconds = time_tolerance_seconds
    
    def run(self):
        try:
            self.reset_instance.deep_search(self.search_times, self.result_found, self.progress_updated, self.time_tolerance_seconds)
        except Exception as e:
            if self.reset_instance:
                self.reset_instance.log(f"Error occurred during deep search: {str(e)}\n")
        finally:
            self.finished.emit()


class FactoryResetGUI(QMainWindow):
    log_signal = pyqtSignal(str)
    add_artifact_data_signal = pyqtSignal(object, object, object, object, object, object, object)

    def __init__(self):
        super().__init__()
        self.reset_instance = None
        self.artifact_data = {}  # Store data for each artifact
        self.use_kst = True  # Default is KST
        self.analysis_running = False  # Whether analysis is running
        self.selected_artifacts = []  # Selected artifact list
        self.confirmed_time_value = None
        self.confirmed_time_dt = None
        self.estimated_reset_time_value = None
        self.estimated_reset_time_source = None
        self.multi_anchor_result = None
        self.MULTI_ANCHOR_CONSISTENCY_MINUTES = 30
        self.artifact_tables = {}
        self.saved_file_path = None  # File path of saved result
        self.saved_source = None  # Source of saved result (ZIP, ADB, Folder)
        self.current_saved_result_filepath = None  # JSON path of currently loaded saved result
        self.current_saved_result_data = None  # Data dict of currently loaded saved result
        self.hidden_artifacts = set()  # Hidden artifact list
        self.hidden_items = {}  # Hidden items: {artifact_id: set(item_keys)}
        self.saved_results_sort_settings = self.load_saved_results_sort_settings()
        self.log_signal.connect(self._append_log_ui)
        self.add_artifact_data_signal.connect(self._add_artifact_data_ui)
        self.init_ui()
        self.apply_modern_theme()
    
    def show_message(self, title, message, icon_type="information"):
        """Show copyable message box"""
        try:
            msg_box = CopyableMessageBox(self, title, message, icon_type)
            msg_box.exec_()
        except Exception as e:
            # Use default QMessageBox on error
            try:
                QMessageBox.information(self, title, message)
            except:
                pass
    
    def show_question(self, title, message):
        """Question message box (Yes/No) - copyable"""
        try:
            # Custom dialog for questions
            dialog = QDialog(self)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(400)
            dialog.setMinimumHeight(200)
            
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # Message text (selectable)
            text_edit = QTextEdit()
            text_edit.setPlainText(message)
            text_edit.setReadOnly(True)
            text_edit.setMaximumHeight(300)
            layout.addWidget(text_edit)
            
            # Button area
            button_layout = QHBoxLayout()
            button_layout.addStretch()
            
            # Copy button
            btn_copy = QPushButton("Copy")
            def copy_text():
                try:
                    clipboard = QApplication.clipboard()
                    clipboard.setText(text_edit.toPlainText())
                except:
                    pass
            btn_copy.clicked.connect(copy_text)
            button_layout.addWidget(btn_copy)
            
            # Yes/No buttons
            btn_yes = QPushButton("Yes")
            btn_yes.clicked.connect(dialog.accept)
            button_layout.addWidget(btn_yes)
            
            btn_no = QPushButton("No")
            btn_no.clicked.connect(dialog.reject)
            button_layout.addWidget(btn_no)
            
            layout.addLayout(button_layout)
            
            if dialog.exec_() == QDialog.Accepted:
                return QMessageBox.Yes
            return QMessageBox.No
        except Exception:
            # Use default QMessageBox on error
            try:
                return QMessageBox.question(self, title, message, QMessageBox.Yes | QMessageBox.No)
            except:
                return QMessageBox.No

    def _as_table_text(self, value):
        """Normalize values for QTableWidgetItem text constructor."""
        if value is None:
            return ""
        return str(value)

    def _saved_results_sort_settings_path(self):
        return os.path.join(os.path.dirname(__file__), "saved_results_sort_settings.json")

    def load_saved_results_sort_settings(self):
        """Load persisted custom sort settings for saved results tree."""
        path = self._saved_results_sort_settings_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def save_saved_results_sort_settings(self):
        """Persist custom sort settings for saved results tree."""
        path = self._saved_results_sort_settings_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.saved_results_sort_settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"[Ex 정렬 설정 저장 실패] {e}")

    def _order_sort_key(self, order_text):
        text = str(order_text or "").strip()
        m_num = re.match(r"^(\d+)\s*차$", text)
        if m_num:
            return (0, int(m_num.group(1)), text.lower())
        m_ex = re.match(r"^Ex(\d+)$", text, re.IGNORECASE)
        if m_ex:
            return (1, int(m_ex.group(1)), text.lower())
        return (2, text.lower())

    def _sorted_orders(self, groups):
        return sorted(groups.keys(), key=self._order_sort_key)

    def _scenario_sort_settings_key(self, order, device_key):
        return f"{order}||{device_key}"

    def _setup_filter_combo(self, combo):
        """Configure a filter combo for both typing and dropdown selection."""
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMaxVisibleItems(20)
        combo.clear()
        combo.addItem("전체")
        completer = combo.completer()
        if completer:
            completer.setCompletionMode(QCompleter.PopupCompletion)

    def _normalized_filter_text(self, combo):
        text = combo.currentText().strip().lower()
        if text in ("", "전체"):
            return ""
        return text

    def _sorted_scenario_keys(self, order, device_key, scenario_groups):
        keys = sorted(scenario_groups.keys())
        if not str(order).lower().startswith("ex"):
            return keys

        custom_order = self.saved_results_sort_settings.get(
            self._scenario_sort_settings_key(order, device_key), []
        )
        if not isinstance(custom_order, list):
            return keys

        ordered = [k for k in custom_order if k in scenario_groups]
        remaining = [k for k in keys if k not in ordered]
        return ordered + remaining

    def _build_saved_results_groups(self, file_list):
        """Build nested groups: order -> device -> scenario -> file list."""
        groups = {}
        for file_info in file_list:
            order = file_info.get('order', '기타')
            manufacturer = file_info.get('manufacturer', '').strip()
            model = file_info.get('model', '').strip()
            scenario = file_info.get('scenario', '').strip() or "(시나리오 없음)"
            display_name = file_info.get('display_name', '').strip()

            device_key = f"{manufacturer} {model}".strip()
            if not device_key:
                device_key = model or display_name or "기타 기기"

            groups.setdefault(order, {})
            groups[order].setdefault(device_key, {})
            groups[order][device_key].setdefault(scenario, [])
            groups[order][device_key][scenario].append(file_info)
        return groups

    def show_ex_order_settings(self):
        """Allow users to customize scenario ordering inside Ex/device groups."""
        try:
            if not hasattr(self, 'all_saved_results') or not self.all_saved_results:
                self.show_message("안내", "설정할 저장 결과가 없습니다.")
                return

            groups = self._build_saved_results_groups(self.all_saved_results)
            ex_orders = [o for o in self._sorted_orders(groups) if str(o).lower().startswith("ex")]

            if not ex_orders:
                self.show_message("안내", "Ex 차수 데이터가 없어 설정할 항목이 없습니다.")
                return

            dialog = QDialog(self)
            dialog.setWindowTitle("Ex 차수 순서 설정")
            dialog.setMinimumWidth(560)
            dialog.setMinimumHeight(500)

            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("Ex 차수와 기기를 선택한 뒤, 시나리오 순서를 위/아래로 조정하세요."))

            order_combo = QComboBox()
            order_combo.addItems(ex_orders)
            layout.addWidget(order_combo)

            device_combo = QComboBox()
            layout.addWidget(device_combo)

            list_widget = QListWidget()
            layout.addWidget(list_widget)

            btn_row = QHBoxLayout()
            btn_up = QPushButton("위로")
            btn_down = QPushButton("아래로")
            btn_row.addWidget(btn_up)
            btn_row.addWidget(btn_down)
            btn_row.addStretch()
            layout.addLayout(btn_row)

            action_row = QHBoxLayout()
            btn_save = QPushButton("저장")
            btn_save.setObjectName("primaryButton")
            btn_cancel = QPushButton("취소")
            action_row.addStretch()
            action_row.addWidget(btn_save)
            action_row.addWidget(btn_cancel)
            layout.addLayout(action_row)

            def load_order_items():
                order = order_combo.currentText()
                device_combo.blockSignals(True)
                device_combo.clear()
                device_combo.addItems(sorted(groups.get(order, {}).keys()))
                device_combo.blockSignals(False)
                load_device_items()

            def load_device_items():
                order = order_combo.currentText()
                device_key = device_combo.currentText()
                scenario_groups = groups.get(order, {}).get(device_key, {})
                keys = self._sorted_scenario_keys(order, device_key, scenario_groups)
                list_widget.clear()
                list_widget.addItems(keys)
                if list_widget.count() > 0:
                    list_widget.setCurrentRow(0)

            def move_selected(delta):
                row = list_widget.currentRow()
                if row < 0:
                    return
                new_row = row + delta
                if new_row < 0 or new_row >= list_widget.count():
                    return
                item = list_widget.takeItem(row)
                list_widget.insertItem(new_row, item)
                list_widget.setCurrentRow(new_row)

            def save_current_order():
                order = order_combo.currentText()
                device_key = device_combo.currentText()
                custom_keys = []
                for i in range(list_widget.count()):
                    custom_keys.append(list_widget.item(i).text())
                self.saved_results_sort_settings[self._scenario_sort_settings_key(order, device_key)] = custom_keys
                self.save_saved_results_sort_settings()

                # Keep current filter state and redraw tree
                self.filter_saved_results()
                dialog.accept()

            order_combo.currentTextChanged.connect(load_order_items)
            device_combo.currentTextChanged.connect(load_device_items)
            btn_up.clicked.connect(lambda: move_selected(-1))
            btn_down.clicked.connect(lambda: move_selected(1))
            btn_save.clicked.connect(save_current_order)
            btn_cancel.clicked.connect(dialog.reject)

            load_order_items()
            dialog.exec_()
        except Exception as e:
            self.log(f"[Ex 차수 순서 설정 오류] {e}")

    def apply_modern_theme(self):
        """Apply a modern and consistent visual theme."""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #f5f7fb;
                color: #1f2937;
                font-family: "Segoe UI", "Malgun Gothic", sans-serif;
                font-size: 10pt;
            }
            QGroupBox {
                background-color: #ffffff;
                border: 1px solid #dbe2ea;
                border-radius: 10px;
                margin-top: 10px;
                padding: 12px 10px 10px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px 0 4px;
                color: #334155;
            }
            QLabel {
                color: #334155;
            }
            QLabel#sectionTitle {
                font-size: 11pt;
                font-weight: 700;
                color: #0f172a;
                padding: 4px 2px;
            }
            QLabel#subtleText {
                color: #64748b;
                font-size: 9pt;
                padding: 2px 0;
            }
            QLineEdit, QTextEdit, QComboBox, QTreeWidget, QTableWidget, QTabWidget::pane {
                background-color: #ffffff;
                border: 1px solid #d0d8e2;
                border-radius: 8px;
                padding: 6px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 1px solid #4f8cff;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border-left: 1px solid #d0d8e2;
                background-color: #f1f5f9;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 7px solid #475569;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                border: 1px solid #d0d8e2;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background-color: #e9eef6;
                color: #334155;
                border: 1px solid #d0d8e2;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 7px 12px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #0f172a;
                font-weight: 600;
            }
            QHeaderView::section {
                background-color: #eef3fb;
                color: #1e293b;
                border: none;
                border-bottom: 1px solid #d7dee8;
                border-right: 1px solid #d7dee8;
                padding: 7px 6px;
                font-weight: 600;
            }
            QTreeWidget, QTableWidget {
                gridline-color: #e6ebf2;
                alternate-background-color: #f8fbff;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
            }
            QPushButton {
                background-color: #e2e8f0;
                color: #0f172a;
                border: 1px solid #d1d8e2;
                border-radius: 8px;
                padding: 6px 12px;
                min-height: 24px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #d7deea;
            }
            QPushButton:pressed {
                background-color: #c8d1df;
            }
            QPushButton:disabled {
                background-color: #edf1f6;
                color: #8a97a8;
                border-color: #dde3eb;
            }
            QPushButton#primaryButton {
                background-color: #2563eb;
                color: #ffffff;
                border: 1px solid #1d4ed8;
            }
            QPushButton#primaryButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton#accentButton {
                background-color: #0ea5a4;
                color: #ffffff;
                border: 1px solid #0b8f8e;
            }
            QPushButton#accentButton:hover {
                background-color: #0b8f8e;
            }
            QPushButton#warnButton {
                background-color: #f59e0b;
                color: #ffffff;
                border: 1px solid #d97706;
            }
            QPushButton#warnButton:hover {
                background-color: #d97706;
            }
            QProgressBar {
                border: 1px solid #d0d8e2;
                border-radius: 8px;
                text-align: center;
                background-color: #eef3fb;
                color: #334155;
                min-height: 16px;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background-color: #2563eb;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #c5cfdb;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
    
    def init_ui(self):
        self.setWindowTitle('Factory Reset Artifact Analyzer')
        self.setGeometry(100, 100, 900, 700)
        
        # 중앙 위젯
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout - split left/right (left: controls+results, right: log)
        main_splitter = QSplitter(Qt.Horizontal)
        central_widget.setLayout(QVBoxLayout())
        central_widget.layout().setContentsMargins(12, 12, 12, 12)
        central_widget.layout().setSpacing(10)
        central_widget.layout().addWidget(main_splitter)
        
        # Left area (controls + results)
        left_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        left_widget.setLayout(main_layout)
        
        # Search target selection group
        source_group = QGroupBox("Search Target")
        source_layout = QVBoxLayout()
        self.source_buttons = QButtonGroup()
        
        self.radio_zip = QRadioButton("1. ZIP file")
        self.radio_adb = QRadioButton("2. live device using adb")
        self.radio_folder = QRadioButton("3. Extracted folder (unzipped folder)")
        
        self.source_buttons.addButton(self.radio_zip, 1)
        self.source_buttons.addButton(self.radio_adb, 2)
        self.source_buttons.addButton(self.radio_folder, 3)
        
        source_layout.addWidget(self.radio_zip)
        source_layout.addWidget(self.radio_adb)
        source_layout.addWidget(self.radio_folder)
        source_group.setLayout(source_layout)
        
        # Search target group size fixed (width flexible, height fixed)
        source_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        source_group.setFixedHeight(100)  # Only height fixed
        
        main_layout.addWidget(source_group)
        
        # File/folder selection area
        file_group = QGroupBox("File/Folder Selection")
        file_layout = QVBoxLayout()
        
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("Select file or folder path...")
        self.file_path_edit.setReadOnly(True)
        
        file_button_layout = QHBoxLayout()
        self.btn_select_file = QPushButton("Select ZIP File")
        self.btn_select_folder = QPushButton("Select Folder")
        self.btn_select_file.setMinimumHeight(34)
        self.btn_select_folder.setMinimumHeight(34)
        self.btn_select_file.clicked.connect(self.select_file)
        self.btn_select_folder.clicked.connect(self.select_folder)
        
        file_button_layout.addWidget(self.btn_select_file)
        file_button_layout.addWidget(self.btn_select_folder)
        
        file_layout.addWidget(self.file_path_edit)
        file_layout.addLayout(file_button_layout)
        file_group.setLayout(file_layout)
        
        # File/folder selection group size fixed (width flexible, height fixed)
        file_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        file_group.setMinimumHeight(120)  # Avoid button text clipping on Windows DPI scaling
        
        main_layout.addWidget(file_group)
        
        # Artifact selection group
        artifact_group = QGroupBox("Artifacts to Find")
        artifact_layout = QVBoxLayout()
        
        # All checkbox separately
        self.checkbox_all = QCheckBox("0. All (Select All)")
        self.checkbox_all.stateChanged.connect(self.toggle_all_artifacts)
        artifact_layout.addWidget(self.checkbox_all)
        
        # Remaining checkboxes in 3 columns
        checkbox_grid = QHBoxLayout()
        
        # First column
        column1 = QVBoxLayout()
        self.checkbox_bootstat_factory_reset = QCheckBox("1-1. bootstat / factory_reset")
        self.checkbox_bootstat_current_time = QCheckBox("1-2. bootstat / factory_reset_current_time")
        self.checkbox_recovery_log = QCheckBox("2-1. recovery.log")
        self.checkbox_last_log = QCheckBox("2-2. last_log")
        self.checkbox_suggestions = QCheckBox("3. suggestions.xml")
        column1.addWidget(self.checkbox_bootstat_factory_reset)
        column1.addWidget(self.checkbox_bootstat_current_time)
        column1.addWidget(self.checkbox_recovery_log)
        column1.addWidget(self.checkbox_last_log)
        column1.addWidget(self.checkbox_suggestions)
        checkbox_grid.addLayout(column1)
        
        # Second column
        column2 = QVBoxLayout()
        self.checkbox_persistent = QCheckBox("4. persistent_properties")
        self.checkbox_appops = QCheckBox("5. appops")
        self.checkbox_wellbing = QCheckBox("6. wellbing")
        self.checkbox_internal = QCheckBox("7. internal")
        column2.addWidget(self.checkbox_persistent)
        column2.addWidget(self.checkbox_appops)
        column2.addWidget(self.checkbox_wellbing)
        column2.addWidget(self.checkbox_internal)
        checkbox_grid.addLayout(column2)
        
        # Third column
        column3 = QVBoxLayout()
        self.checkbox_err = QCheckBox("8. eRR.p")
        self.checkbox_ulr = QCheckBox("9. ULR_PERSISTENT_PREFS.xml")
        column3.addWidget(self.checkbox_err)
        column3.addWidget(self.checkbox_ulr)
        column3.addStretch()  # Fill remaining space
        checkbox_grid.addLayout(column3)
        
        self.artifact_checkboxes = [
            self.checkbox_bootstat_factory_reset,
            self.checkbox_bootstat_current_time,
            self.checkbox_recovery_log,
            self.checkbox_last_log,
            self.checkbox_suggestions,
            self.checkbox_persistent,
            self.checkbox_appops,
            self.checkbox_wellbing,
            self.checkbox_internal,
            self.checkbox_err,
            self.checkbox_ulr
        ]
        
        # Checkbox to artifact_id mapping
        self.checkbox_to_artifact_id = {
            self.checkbox_bootstat_factory_reset: "1",
            self.checkbox_bootstat_current_time: "1",
            self.checkbox_recovery_log: "21",
            self.checkbox_last_log: "22",
            self.checkbox_suggestions: "3",
            self.checkbox_persistent: "4",
            self.checkbox_appops: "5",
            self.checkbox_wellbing: "6",
            self.checkbox_internal: "7",
            self.checkbox_err: "8",
            self.checkbox_ulr: "9"
        }
        
        # Connect real-time filtering to each checkbox
        for checkbox in self.artifact_checkboxes:
            checkbox.stateChanged.connect(self.on_artifact_filter_changed)
        
        artifact_layout.addLayout(checkbox_grid)
        artifact_group.setLayout(artifact_layout)
        
        # Artifact group size fixed (width flexible, height fixed)
        artifact_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        artifact_group.setFixedHeight(210)  # Only height fixed
        
        main_layout.addWidget(artifact_group)
        
        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        self.btn_run = QPushButton("Run Analysis")
        self.btn_run.clicked.connect(self.run_analysis)
        self.btn_run.setObjectName("primaryButton")
        
        self.btn_deep_search = QPushButton("Deep Search")
        self.btn_deep_search.clicked.connect(self.run_deep_search)
        self.btn_deep_search.setObjectName("accentButton")
        self.btn_deep_search.setEnabled(False)  # Enabled after analysis completes
        
        self.btn_view_saved = QPushButton("View Saved Results")
        self.btn_view_saved.clicked.connect(self.show_saved_results)
        self.btn_view_saved.setObjectName("warnButton")
        
        self.btn_item_settings = QPushButton("Item Visibility Settings")
        self.btn_item_settings.clicked.connect(self.show_item_visibility_settings)
        self.btn_item_settings.setObjectName("accentButton")
        
        button_layout.addWidget(self.btn_run)
        button_layout.addWidget(self.btn_deep_search)
        button_layout.addWidget(self.btn_view_saved)
        button_layout.addWidget(self.btn_item_settings)
        main_layout.addLayout(button_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(30)  # Minimum height
        self.progress_bar.setMaximumHeight(30)  # Maximum height (fixed)
        main_layout.addWidget(self.progress_bar)
        
        # Timezone selection checkbox
        timezone_layout = QHBoxLayout()
        self.checkbox_kst = QCheckBox("KST (Korea Time, UTC+9)")
        self.checkbox_kst.setChecked(True)
        self.checkbox_kst.stateChanged.connect(self.on_timezone_changed)
        timezone_layout.addWidget(self.checkbox_kst)
        timezone_layout.addStretch()
        main_layout.addLayout(timezone_layout)

        # Confirmed reset time display area
        confirmed_layout = QHBoxLayout()
        self.confirmed_time_label = QLabel("Confirmed Reset Time:")
        self.confirmed_time_display = QLineEdit()
        self.confirmed_time_display.setReadOnly(True)
        self.confirmed_time_display.setPlaceholderText("No time selected.")
        self.btn_set_confirmed = QPushButton("Confirm Selected Time")
        self.btn_set_confirmed.clicked.connect(self.set_confirmed_time_from_selection)
        self.btn_clear_confirmed = QPushButton("Clear Confirmed Time")
        self.btn_clear_confirmed.clicked.connect(self.clear_confirmed_time)
        confirmed_layout.addWidget(self.confirmed_time_label)
        confirmed_layout.addWidget(self.confirmed_time_display)
        confirmed_layout.addWidget(self.btn_set_confirmed)
        confirmed_layout.addWidget(self.btn_clear_confirmed)
        main_layout.addLayout(confirmed_layout)

        estimated_layout = QHBoxLayout()
        self.estimated_reset_time_label = QLabel("Estimated Final Reset Time:")
        self.estimated_reset_time_display = QLineEdit()
        self.estimated_reset_time_display.setReadOnly(True)
        self.estimated_reset_time_display.setPlaceholderText("No estimated time yet.")
        self.estimated_reset_time_source_label = QLabel("Source: -")
        self.estimated_reset_time_source_label.setObjectName("subtleText")
        estimated_layout.addWidget(self.estimated_reset_time_label)
        estimated_layout.addWidget(self.estimated_reset_time_display)
        estimated_layout.addWidget(self.estimated_reset_time_source_label)
        main_layout.addLayout(estimated_layout)

        # Multi-anchor cross-validation (논문 §3.4)
        cross_val_group = QGroupBox("다중 시간 앵커 교차검증")
        cross_val_layout = QVBoxLayout()
        cross_val_group.setLayout(cross_val_layout)
        self.cross_validation_grade_label = QLabel("신뢰 등급: -")
        self.cross_validation_grade_label.setWordWrap(True)
        self.cross_validation_detail_label = QLabel("분석 후 자동 갱신됩니다.")
        self.cross_validation_detail_label.setObjectName("subtleText")
        self.cross_validation_detail_label.setWordWrap(True)
        self.cross_validation_span_label = QLabel("시간 분포: -")
        self.cross_validation_span_label.setObjectName("subtleText")
        self.cross_validation_span_label.setWordWrap(True)
        self.cross_anchor_table = QTableWidget()
        self.cross_anchor_table.setColumnCount(5)
        self.cross_anchor_table.setHorizontalHeaderLabels(["구성요소", "등급", "시각", "오프셋(분)", "분포 막대"])
        self.cross_anchor_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cross_anchor_table.setSortingEnabled(False)
        self.cross_anchor_table.setAlternatingRowColors(True)
        self.cross_anchor_table.verticalHeader().setVisible(False)
        self.cross_anchor_table.setMinimumHeight(180)
        self.cross_anchor_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.cross_anchor_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.cross_anchor_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.cross_anchor_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.cross_anchor_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        cross_val_btn_layout = QHBoxLayout()
        self.btn_cross_validation_report = QPushButton("교차검증 보고서")
        self.btn_cross_validation_report.setObjectName("accentButton")
        self.btn_cross_validation_report.clicked.connect(self.show_cross_validation_report)
        self.btn_oem_matrix = QPushButton("제조사별 아티팩트")
        self.btn_oem_matrix.clicked.connect(self.show_oem_artifact_matrix)
        cross_val_btn_layout.addWidget(self.btn_cross_validation_report)
        cross_val_btn_layout.addWidget(self.btn_oem_matrix)
        cross_val_layout.addWidget(self.cross_validation_grade_label)
        cross_val_layout.addWidget(self.cross_validation_detail_label)
        cross_val_layout.addWidget(self.cross_validation_span_label)
        cross_val_layout.addWidget(self.cross_anchor_table)
        cross_val_layout.addLayout(cross_val_btn_layout)
        main_layout.addWidget(cross_val_group)
        
        # Text area for log output (below confirmed reset time, bottom right)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFontFamily("Courier")
        self.result_text.setVisible(True)  # Show log
        main_layout.addWidget(self.result_text)
        
        # Add left area to splitter
        main_splitter.addWidget(left_widget)
        
        # Center: Analysis results tabs (takes up left space)
        self.result_tabs = QTabWidget()
        self.result_tabs.currentChanged.connect(self.apply_confirmed_time_highlight)
        main_splitter.addWidget(self.result_tabs)
        
        # Right: Saved results tree
        saved_results_widget = QWidget()
        saved_results_layout = QVBoxLayout()
        saved_results_widget.setLayout(saved_results_layout)
        
        saved_label = QLabel("Saved Analysis Results")
        saved_label.setObjectName("sectionTitle")
        saved_results_layout.addWidget(saved_label)
        
        # Filter area
        filter_group = QGroupBox("Filter")
        filter_layout = QVBoxLayout()
        filter_group.setLayout(filter_layout)
        
        # Order filter
        order_filter_layout = QHBoxLayout()
        order_filter_layout.addWidget(QLabel("Order:"))
        self.filter_order_combo = QComboBox()
        self._setup_filter_combo(self.filter_order_combo)
        self.filter_order_combo.currentTextChanged.connect(self.filter_saved_results)
        order_filter_layout.addWidget(self.filter_order_combo)
        filter_layout.addLayout(order_filter_layout)
        
        # Manufacturer filter
        manufacturer_filter_layout = QHBoxLayout()
        manufacturer_filter_layout.addWidget(QLabel("Manufacturer:"))
        self.filter_manufacturer_combo = QComboBox()
        self._setup_filter_combo(self.filter_manufacturer_combo)
        self.filter_manufacturer_combo.currentTextChanged.connect(self.filter_saved_results)
        manufacturer_filter_layout.addWidget(self.filter_manufacturer_combo)
        filter_layout.addLayout(manufacturer_filter_layout)
        
        # Model filter
        model_filter_layout = QHBoxLayout()
        model_filter_layout.addWidget(QLabel("Model:"))
        self.filter_model_combo = QComboBox()
        self._setup_filter_combo(self.filter_model_combo)
        self.filter_model_combo.currentTextChanged.connect(self.filter_saved_results)
        model_filter_layout.addWidget(self.filter_model_combo)
        filter_layout.addLayout(model_filter_layout)
        
        # Scenario filter
        scenario_filter_layout = QHBoxLayout()
        scenario_filter_layout.addWidget(QLabel("Scenario:"))
        self.filter_scenario_combo = QComboBox()
        self._setup_filter_combo(self.filter_scenario_combo)
        self.filter_scenario_combo.currentTextChanged.connect(self.filter_saved_results)
        scenario_filter_layout.addWidget(self.filter_scenario_combo)
        filter_layout.addLayout(scenario_filter_layout)
        
        # Clear filter button
        btn_clear_filter = QPushButton("Clear Filter")
        btn_clear_filter.clicked.connect(self.clear_saved_results_filter)
        filter_layout.addWidget(btn_clear_filter)
        
        saved_results_layout.addWidget(filter_group)
        
        self.saved_results_tree = QTreeWidget()
        self.saved_results_tree.setHeaderLabels(["Saved Results"])
        self.saved_results_tree.setRootIsDecorated(True)
        self.saved_results_tree.setMinimumWidth(350)  # Increase minimum width of saved results tree
        self.saved_results_tree.itemSelectionChanged.connect(self.on_saved_result_selected)
        self.saved_results_tree.itemDoubleClicked.connect(self.on_saved_result_double_clicked)
        saved_results_layout.addWidget(self.saved_results_tree)

        # Re-map original source path when opening saved results on another PC
        saved_source_group = QGroupBox("원본 소스 경로 (다른 PC)")
        saved_source_layout = QVBoxLayout()
        saved_source_group.setLayout(saved_source_layout)

        self.saved_source_status_label = QLabel("저장된 결과를 선택하면 원본 경로가 표시됩니다.")
        self.saved_source_status_label.setObjectName("subtleText")
        self.saved_source_status_label.setWordWrap(True)
        saved_source_layout.addWidget(self.saved_source_status_label)

        saved_source_type_layout = QHBoxLayout()
        saved_source_type_layout.addWidget(QLabel("소스:"))
        self.saved_source_combo = QComboBox()
        self.saved_source_combo.addItem("ZIP", "1")
        self.saved_source_combo.addItem("ADB", "2")
        self.saved_source_combo.addItem("Folder", "3")
        self.saved_source_combo.currentIndexChanged.connect(self._on_saved_source_combo_changed)
        saved_source_type_layout.addWidget(self.saved_source_combo)
        saved_source_layout.addLayout(saved_source_type_layout)

        saved_source_path_layout = QHBoxLayout()
        self.saved_source_path_edit = QLineEdit()
        self.saved_source_path_edit.setPlaceholderText("예: D:/case/EXTRACTION_FFS.zip 또는 D:/case/folder")
        btn_browse_saved_source = QPushButton("찾아보기")
        btn_browse_saved_source.clicked.connect(self.browse_saved_source_path)
        saved_source_path_layout.addWidget(self.saved_source_path_edit)
        saved_source_path_layout.addWidget(btn_browse_saved_source)
        saved_source_layout.addLayout(saved_source_path_layout)

        saved_source_btn_layout = QHBoxLayout()
        btn_apply_saved_source = QPushButton("경로 적용")
        btn_apply_saved_source.setObjectName("accentButton")
        btn_apply_saved_source.clicked.connect(self.apply_saved_source_path)
        btn_save_saved_source = QPushButton("JSON 저장")
        btn_save_saved_source.clicked.connect(self.persist_saved_source_path)
        saved_source_btn_layout.addWidget(btn_apply_saved_source)
        saved_source_btn_layout.addWidget(btn_save_saved_source)
        saved_source_layout.addLayout(saved_source_btn_layout)

        saved_results_layout.addWidget(saved_source_group)
        
        # Store all data for filtering
        self.all_saved_results = []
        
        # Saved results management buttons
        saved_btn_layout = QHBoxLayout()
        btn_refresh_saved = QPushButton("Refresh")
        btn_refresh_saved.setObjectName("accentButton")
        btn_refresh_saved.clicked.connect(self.load_saved_results)
        btn_delete_saved = QPushButton("Delete")
        btn_delete_saved.setObjectName("warnButton")
        btn_delete_saved.clicked.connect(self.delete_saved_result)
        btn_ex_order_settings = QPushButton("Ex 순서 설정")
        btn_ex_order_settings.clicked.connect(self.show_ex_order_settings)
        btn_export_saved = QPushButton("일괄 보내기")
        btn_export_saved.setObjectName("primaryButton")
        btn_export_saved.clicked.connect(self.show_export_saved_results_dialog)
        saved_btn_layout.addWidget(btn_refresh_saved)
        saved_btn_layout.addWidget(btn_delete_saved)
        saved_btn_layout.addWidget(btn_ex_order_settings)
        saved_btn_layout.addWidget(btn_export_saved)
        saved_results_layout.addLayout(saved_btn_layout)
        
        # Save information display
        save_info_label = QLabel("Auto saved when analysis completes\n(Save location: saved_results folder)")
        save_info_label.setObjectName("subtleText")
        save_info_label.setWordWrap(True)
        saved_results_layout.addWidget(save_info_label)
        
        main_splitter.addWidget(saved_results_widget)
        
        # Main splitter size settings (left controls: 400, analysis results: 1000, right saved results: 400)
        main_splitter.setSizes([400, 1000, 400])
        
        # Initial load of saved results list
        self.load_saved_results()
        
        # Create summary results tab (added at the front)
        summary_tab = QWidget()
        summary_layout = QVBoxLayout()
        summary_tab.setLayout(summary_layout)
        
        summary_table = QTableWidget()
        summary_table.setColumnCount(5)
        summary_table.setHorizontalHeaderLabels(["Artifact", "Item", "Path", "Time", "Original Time"])
        summary_table.horizontalHeader().setStretchLastSection(True)
        summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        summary_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        summary_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        summary_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        summary_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        summary_table.setAlternatingRowColors(True)
        summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        summary_table.setSortingEnabled(True)  # Enable sorting (click header to sort)
        # Use a wrapper function to safely handle clicks with exception handling
        def safe_show_summary_detail(row, col):
            try:
                import sys
                print(f"[DEBUG] safe_show_summary_detail called: row={row}, col={col}", file=sys.stderr)
                sys.stderr.flush()
                # Snapshot values immediately (avoid holding QTableWidgetItem refs while sorting)
                was_sorting = summary_table.isSortingEnabled()
                try:
                    if was_sorting:
                        summary_table.setSortingEnabled(False)
                    def _txt(c):
                        try:
                            it = summary_table.item(row, c)
                            return it.text() if it else ""
                        except Exception:
                            return ""
                    artifact_name = _txt(0)
                    item_name = _txt(1)
                    file_path = _txt(2)
                    time_value = _txt(3)
                    original_time = _txt(4)
                finally:
                    if was_sorting:
                        summary_table.setSortingEnabled(True)

                if not file_path or file_path.strip() == "":
                    print("[DEBUG] safe_show_summary_detail: empty file_path, skipping", file=sys.stderr)
                    sys.stderr.flush()
                    return

                match_hint = original_time or time_value
                header_text = (
                    f"아티팩트: {artifact_name}\n"
                    f"항목: {item_name}\n"
                    f"파일 경로: {file_path}\n"
                    f"시간: {time_value}\n"
                    f"원본 시간: {original_time}"
                )

                # Defer heavy UI work to next event loop tick (avoids re-entrancy crashes)
                def _open():
                    try:
                        self.show_raw_hex_dialog("Summary Results Details", header_text, file_path, match_hint, context_item_name=item_name)
                    except Exception as e:
                        import traceback
                        em = f"[ERROR] safe_show_summary_detail deferred open failed: {e}\n{traceback.format_exc()}"
                        print(em, file=sys.stderr)
                        sys.stderr.flush()
                        try:
                            self.show_message("Error", f"Error showing summary detail: {str(e)}")
                        except Exception:
                            pass
                QTimer.singleShot(0, _open)
            except Exception as e:
                import sys
                import traceback
                error_msg = f"Error in show_summary_detail: {e}\n{traceback.format_exc()}"
                print(error_msg, file=sys.stderr)
                sys.stderr.flush()
                try:
                    if hasattr(self, 'log'):
                        self.log(f"Error in show_summary_detail: {e}")
                        self.log(traceback.format_exc())
                except:
                    pass
                try:
                    if hasattr(self, 'show_message'):
                        self.show_message("Error", f"Error showing summary detail: {str(e)}")
                except:
                    pass
        summary_table.cellClicked.connect(safe_show_summary_detail)
        summary_table.sortByColumn(3, Qt.AscendingOrder)  # Initial sort by time column
        
        summary_layout.addWidget(summary_table)
        self.summary_table = summary_table
        self.summary_tab_widget = summary_tab
        self.result_tabs.addTab(summary_tab, "✓ Summary Results")
        
        # Create deep search results tab
        deep_search_tab = QWidget()
        deep_search_layout = QVBoxLayout()
        deep_search_tab.setLayout(deep_search_layout)
        
        deep_search_table = QTableWidget()
        deep_search_table.setColumnCount(4)
        deep_search_table.setHorizontalHeaderLabels(["Search Time", "File Path", "Match Format", "Match Value"])
        deep_search_table.horizontalHeader().setStretchLastSection(False)
        deep_search_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        deep_search_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)  # User can resize
        deep_search_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        deep_search_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)  # User can resize
        # Initial column size settings
        deep_search_table.setColumnWidth(1, 400)  # Initial width of file path column
        deep_search_table.setColumnWidth(3, 200)  # Initial width of match value column
        deep_search_table.setAlternatingRowColors(True)
        deep_search_table.setEditTriggers(QTableWidget.NoEditTriggers)
        deep_search_table.setSortingEnabled(True)  # Enable sorting (click header to sort)
        # Use a wrapper function to safely handle clicks with exception handling
        def safe_show_deep_search_detail(row, col):
            try:
                import sys
                print(f"[DEBUG] safe_show_deep_search_detail called: row={row}, col={col}", file=sys.stderr)
                sys.stderr.flush()
                # Snapshot values immediately (avoid holding QTableWidgetItem refs while sorting)
                was_sorting = deep_search_table.isSortingEnabled()
                try:
                    if was_sorting:
                        deep_search_table.setSortingEnabled(False)
                    def _txt(c):
                        try:
                            it = deep_search_table.item(row, c)
                            return it.text() if it else ""
                        except Exception:
                            return ""
                    search_time = _txt(0)
                    file_path = _txt(1)
                    match_format = _txt(2)
                    match_item = deep_search_table.item(row, 3)
                    match_value = _txt(3)
                    raw_match_value = match_item.data(Qt.UserRole) if match_item else match_value
                finally:
                    if was_sorting:
                        deep_search_table.setSortingEnabled(True)

                if not file_path or file_path.strip() == "":
                    print("[DEBUG] safe_show_deep_search_detail: empty file_path, skipping", file=sys.stderr)
                    sys.stderr.flush()
                    return

                def _open():
                    try:
                        self._show_deep_search_detail_from_values(
                            search_time=search_time,
                            file_path=file_path,
                            match_format=match_format,
                            match_value=match_value,
                            raw_match_value=raw_match_value,
                        )
                    except Exception as e:
                        import traceback
                        em = f"[ERROR] safe_show_deep_search_detail deferred open failed: {e}\n{traceback.format_exc()}"
                        print(em, file=sys.stderr)
                        sys.stderr.flush()
                        try:
                            self.show_message("Error", f"Error showing deep search detail: {str(e)}")
                        except Exception:
                            pass
                QTimer.singleShot(0, _open)
            except Exception as e:
                import sys
                import traceback
                error_msg = f"Error in show_deep_search_detail: {e}\n{traceback.format_exc()}"
                print(error_msg, file=sys.stderr)
                sys.stderr.flush()
                try:
                    if hasattr(self, 'log'):
                        self.log(f"Error in show_deep_search_detail: {e}")
                        self.log(traceback.format_exc())
                except:
                    pass
                try:
                    if hasattr(self, 'show_message'):
                        self.show_message("Error", f"Error showing deep search detail: {str(e)}")
                except:
                    pass
        deep_search_table.cellClicked.connect(safe_show_deep_search_detail)
        
        deep_search_layout.addWidget(deep_search_table)
        self.deep_search_table = deep_search_table
        self.deep_search_tab_widget = deep_search_tab  # Store deep search results tab widget
        self.result_tabs.addTab(deep_search_tab, "Deep Search Results")
        
        # Create tabs for each artifact
        self.artifact_tables = {}
        self.artifact_tab_widgets = {}  # artifact_id -> tab widget mapping
        self.artifact_names = {
            "1": "bootstat",
            "21": "recovery.log",
            "22": "last_log",
            "3": "suggestions.xml",
            "4": "persistent_properties",
            "5": "appops",
            "6": "wellbing",
            "7": "internal",
            "8": "eRR.p",
            "9": "ULR_PERSISTENT_PREFS.xml"
        }
        
        for artifact_id, artifact_name in self.artifact_names.items():
            tab = QWidget()
            tab_layout = QVBoxLayout()
            tab.setLayout(tab_layout)
            
            table = QTableWidget()
            table.setColumnCount(5)  # Added checkbox column
            table.setHorizontalHeaderLabels(["", "Item", "Path", "Time", "Original Time"])
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Checkbox column
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Item
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # Path
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Time
            table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Original Time
            table.setAlternatingRowColors(True)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            # Use a wrapper function to safely handle clicks with exception handling
            def safe_show_artifact_detail(row, col):
                try:
                    import sys
                    print(f"[DEBUG] safe_show_artifact_detail called: row={row}, col={col}", file=sys.stderr)
                    sys.stderr.flush()
                    if col == 0:
                        # checkbox column
                        return
                    # Snapshot values immediately (avoid holding QTableWidgetItem refs)
                    def _txt(c):
                        try:
                            it = table.item(row, c)
                            return it.text() if it else ""
                        except Exception:
                            return ""
                    item_name = _txt(1)
                    file_path = _txt(2)
                    time_value = _txt(3)
                    original_time = _txt(4)
                    if not file_path or file_path.strip() == "":
                        print("[DEBUG] safe_show_artifact_detail: empty file_path, skipping", file=sys.stderr)
                        sys.stderr.flush()
                        return
                    match_hint = original_time or time_value
                    header_text = (
                        f"항목: {item_name}\n"
                        f"파일 경로: {file_path}\n"
                        f"시간: {time_value}\n"
                        f"원본 시간: {original_time}"
                    )
                    abx_text = None
                    try:
                        if item_name and "appops" in item_name.lower() and self.reset_instance:
                            abx_text = getattr(self.reset_instance, "last_abx_output", None)
                    except Exception:
                        abx_text = None

                    def _open():
                        try:
                            self.show_raw_hex_dialog("아티팩트 상세", header_text, file_path, match_hint, abx_text=abx_text)
                        except Exception as e:
                            import traceback
                            em = f"[ERROR] safe_show_artifact_detail deferred open failed: {e}\n{traceback.format_exc()}"
                            print(em, file=sys.stderr)
                            sys.stderr.flush()
                            try:
                                self.show_message("Error", f"Error showing artifact detail: {str(e)}")
                            except Exception:
                                pass
                    QTimer.singleShot(0, _open)
                except Exception as e:
                    import sys
                    import traceback
                    error_msg = f"Error in show_artifact_detail: {e}\n{traceback.format_exc()}"
                    print(error_msg, file=sys.stderr)
                    sys.stderr.flush()
                    try:
                        if hasattr(self, 'log'):
                            self.log(f"Error in show_artifact_detail: {e}")
                            self.log(traceback.format_exc())
                    except:
                        pass
                    try:
                        if hasattr(self, 'show_message'):
                            self.show_message("Error", f"Error showing artifact detail: {str(e)}")
                    except:
                        pass
            table.cellClicked.connect(safe_show_artifact_detail)
            # Set context menu for table rows
            table.setContextMenuPolicy(Qt.CustomContextMenu)
            table.customContextMenuRequested.connect(lambda pos, t=table, aid=artifact_id: self.show_table_row_context_menu(pos, t, aid))
            # Connect cell changed to handle checkbox state changes (only for checkbox column)
            table.cellChanged.connect(lambda r, c, t=table, aid=artifact_id: self.on_table_cell_changed(t, r, c, aid) if c == 0 else None)
            
            tab_layout.addWidget(table)
            self.artifact_tables[artifact_id] = table
            self.artifact_tab_widgets[artifact_id] = tab
            # Initially show "(Waiting)"
            self.result_tabs.addTab(tab, f"{artifact_name} (Waiting)")
        
        # 탭 우클릭 메뉴 설정
        self.result_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_tabs.customContextMenuRequested.connect(self.show_tab_context_menu)
        
        
        # 초기 상태 설정
        self.radio_zip.setChecked(True)
        self.checkbox_all.setChecked(True)
        self.toggle_all_artifacts(Qt.Checked)
    
    def toggle_all_artifacts(self, state):
        """Toggle all artifact checkboxes based on All checkbox state"""
        checked = (state == Qt.Checked)
        # Temporarily block signals to prevent filtering from running multiple times
        for checkbox in self.artifact_checkboxes:
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        # Run filtering only once
        self.apply_artifact_filter()
    
    def on_artifact_filter_changed(self):
        """Called when artifact filter checkbox state changes"""
        sender = self.sender()
        # Keep "All" checkbox synchronized with individual artifact checkboxes.
        if sender != self.checkbox_all:
            all_checked = all(cb.isChecked() for cb in self.artifact_checkboxes)
            if self.checkbox_all.isChecked() != all_checked:
                self.checkbox_all.blockSignals(True)
                self.checkbox_all.setChecked(all_checked)
                self.checkbox_all.blockSignals(False)
        self.apply_artifact_filter()
    
    def apply_artifact_filter(self):
        """Filter artifact tabs and summary results in real-time based on checkbox state"""
        # Skip if result_tabs is not yet initialized
        if not hasattr(self, 'result_tabs') or not self.result_tabs:
                return

        # Collect checked artifact IDs
        visible_artifact_ids = set()
        
        # Show all artifacts if All checkbox is checked
        if self.checkbox_all.isChecked():
            visible_artifact_ids = set(self.artifact_names.keys())
        else:
            for checkbox, artifact_id in self.checkbox_to_artifact_id.items():
                if checkbox.isChecked():
                    visible_artifact_ids.add(artifact_id)
        
        # Removed verbose filter applied logging
        
        # Remove all artifact tabs and re-add only visible ones
        tabs_to_restore = {}  # artifact_id -> (widget, tab_text)
        
        # First find and remove all artifact tabs (remove in reverse order to avoid index issues)
        indices_to_remove = []
        for i in range(self.result_tabs.count() - 1, -1, -1):
            widget = self.result_tabs.widget(i)
            if widget in self.artifact_tab_widgets.values():
                # Artifact tab
                artifact_id = None
                for aid, w in self.artifact_tab_widgets.items():
                    if w == widget:
                        artifact_id = aid
                        break
                
                if artifact_id:
                    # Don't remove hidden artifacts
                    if artifact_id in self.hidden_artifacts:
                        continue
                    
                    tab_text = self.result_tabs.tabText(i)
                    indices_to_remove.append((i, artifact_id, widget, tab_text))
        
        # Remove tabs (in reverse order)
        for i, artifact_id, widget, tab_text in indices_to_remove:
            self.result_tabs.removeTab(i)
            # Store tabs that should be displayed for later re-addition
            if artifact_id in visible_artifact_ids:
                tabs_to_restore[artifact_id] = (widget, tab_text)
        
        # Re-add tabs to display (maintain original order)
        for artifact_id in self.artifact_names.keys():
            if artifact_id in visible_artifact_ids and artifact_id not in self.hidden_artifacts:
                widget = self.artifact_tab_widgets.get(artifact_id)
                if widget:
                    # Check if already added
                    if self.result_tabs.indexOf(widget) < 0:
                        # Add at appropriate position (between other artifact tabs)
                        insert_index = len(self.artifact_tab_widgets)
                        for i in range(self.result_tabs.count()):
                            w = self.result_tabs.widget(i)
                            if w in self.artifact_tab_widgets.values():
                                insert_index = i + 1
                                break
                        
                        # Determine tab name
                        if artifact_id in tabs_to_restore:
                            tab_text = tabs_to_restore[artifact_id][1]
                        else:
                            artifact_name = self.artifact_names.get(artifact_id, artifact_id)
                            # Check current tab name (set by update_table)
                            tab_text = f"{artifact_name} (Waiting)"
                            # Check status if artifact_data exists
                            if artifact_id in self.artifact_data:
                                data_list = self.artifact_data[artifact_id]
                                has_time = any(d.get('time') for d in data_list)
                                if has_time:
                                    tab_text = f"✓ {artifact_name}"
                                elif data_list:
                                    tab_text = f"✗ {artifact_name} (No Data)"
                        
                        self.result_tabs.insertTab(insert_index, widget, tab_text)
                        # Removed verbose tab added logging
        
        # Update summary results table
        self.update_summary_table()
    
    def is_artifact_visible(self, artifact_id):
        """Check if artifact should be visible based on current filter"""
        # Show all artifacts if All checkbox is checked
        if self.checkbox_all.isChecked():
            return True
        
        # Check checkbox state
        has_mapping = False
        any_checked = False
        for checkbox, aid in self.checkbox_to_artifact_id.items():
            if aid == artifact_id:
                has_mapping = True
                if checkbox.isChecked():
                    any_checked = True
        if has_mapping:
            return any_checked
        return False

    def is_bootstat_item_visible(self, item_name):
        """Check if bootstat sub-item is enabled in Artifacts to Find."""
        if self.checkbox_all.isChecked():
            return True
        name = str(item_name or "").strip().lower()
        show_factory_reset = self.checkbox_bootstat_factory_reset.isChecked()
        show_factory_reset_current = self.checkbox_bootstat_current_time.isChecked()
        if "factory_reset_current_time" in name:
            return show_factory_reset_current
        if "factory_reset" in name:
            return show_factory_reset
        # Fallback for unexpected names
        return show_factory_reset or show_factory_reset_current

    def select_file(self):
        """Select ZIP file"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select ZIP File", "", "ZIP Files (*.zip)")
        if file_path:
            # Reset previous analysis state
            self.reset_analysis_state()
            self.file_path_edit.setText(file_path)
            self.load_confirmed_time()
    
    def select_folder(self):
        """Select folder"""
        folder_path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder_path:
            # Reset previous analysis state
            self.reset_analysis_state()
            self.file_path_edit.setText(folder_path)
            self.load_confirmed_time()
    
    def reset_analysis_state(self):
        """Reset analysis state (called when new file is selected)"""
        # Clean up previous analysis instance
        if hasattr(self, 'worker_thread') and self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.terminate()
            self.worker_thread.wait()
        if hasattr(self, 'deep_search_thread') and self.deep_search_thread and self.deep_search_thread.isRunning():
            self.deep_search_thread.terminate()
            self.deep_search_thread.wait()
        
        self.reset_instance = None
        
        # Initialize artifact data
        self.artifact_data = {}
        for artifact_id in self.artifact_tables.keys():
            self.clear_artifact_data(artifact_id)
        
        # Initialize result text
        if hasattr(self, 'result_text') and self.result_text:
            self.result_text.clear()

        if hasattr(self, 'summary_table') and self.summary_table:
            self.summary_table.setRowCount(0)
        if hasattr(self, 'estimated_reset_time_display') and self.estimated_reset_time_display:
            self.update_estimated_reset_time_display()
        
        # Hide progress bar
        if hasattr(self, 'progress_bar') and self.progress_bar:
            self.progress_bar.setVisible(False)
        
        # Initialize button state
        if hasattr(self, 'btn_run') and self.btn_run:
            self.btn_run.setEnabled(True)
        if hasattr(self, 'btn_deep_search') and self.btn_deep_search:
            self.btn_deep_search.setEnabled(False)
        
        # Initialize tab names
        self.reorder_tabs()

    def log(self, message):
        """Thread-safe GUI log output."""
        text = str(message)
        if QThread.currentThread() != self.thread():
            self.log_signal.emit(text)
            return
        self._append_log_ui(text)

    def _append_log_ui(self, message):
        """Append log text on UI thread."""
        if hasattr(self, "result_text") and self.result_text:
            self.result_text.append(message)

    def get_confirmed_time_key(self):
        """Key for saving settings"""
        path = self.file_path_edit.text().strip()
        if path:
            return os.path.abspath(path)
        return "ADB"

    def load_confirmed_time(self):
        """Load confirmed time from settings file"""
        config_path = os.path.join(os.path.dirname(__file__), "confirmed_time_settings.json")
        key = self.get_confirmed_time_key()
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
        except Exception:
            data = {}
        self.confirmed_time_value = data.get(key)
        if self.confirmed_time_value:
            self.confirmed_time_dt = self.parse_time_text(self.confirmed_time_value)
            # Removed verbose confirmed time loaded logging
        self.update_confirmed_time_display()
        self.apply_confirmed_time_highlight()

    def save_confirmed_time(self):
        """Save confirmed time"""
        config_path = os.path.join(os.path.dirname(__file__), "confirmed_time_settings.json")
        key = self.get_confirmed_time_key()
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
        except Exception:
            data = {}
        if self.confirmed_time_value:
            data[key] = self.confirmed_time_value
        else:
            data.pop(key, None)
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def update_confirmed_time_display(self):
        if self.confirmed_time_value:
            self.confirmed_time_display.setText(self.confirmed_time_value)
        else:
            self.confirmed_time_display.setText("")

    def format_time_for_display(self, time_value, is_kst=False):
        """Convert stored datetime to current timezone display text."""
        if not time_value or not isinstance(time_value, datetime):
            return None, ""

        if is_kst:
            if self.use_kst:
                display_time = time_value
                suffix = "KST"
            else:
                display_time = time_value - timedelta(hours=9)
                suffix = "UTC"
        else:
            if self.use_kst:
                display_time = time_value + timedelta(hours=9)
                suffix = "KST"
            else:
                display_time = time_value
                suffix = "UTC"

        return display_time, display_time.strftime(f"%Y-%m-%d %H:%M:%S {suffix}")

    def get_estimated_reset_time_info(self):
        """Return the best available reset-time estimate for the main screen."""
        candidates = []

        for artifact_id, data_list in self.artifact_data.items():
            if artifact_id in self.hidden_artifacts:
                continue
            if not self.is_artifact_visible(artifact_id):
                continue

            artifact_name = self.artifact_names.get(artifact_id, artifact_id)
            for data in data_list:
                item_key = self._get_item_key(data)
                hidden_items = self.hidden_items.get(artifact_id, set())
                if item_key in hidden_items:
                    continue
                if artifact_id == "1" and not self.is_bootstat_item_visible(data.get('name', '')):
                    continue

                time_value = data.get('time')
                if not time_value or not isinstance(time_value, datetime):
                    continue

                display_time, time_str = self.format_time_for_display(
                    time_value,
                    data.get('is_kst', False)
                )
                if not display_time:
                    continue

                item_name = str(data.get('name', '') or '')
                item_name_lower = item_name.lower()
                original_time_text = str(data.get('original_time', '') or '').lower()
                priority = None

                # recovery/last_log의 "완료" 이벤트가 실제 최종 리셋 시각에 가장 가깝다.
                if artifact_id in {"21", "22"}:
                    if any(keyword in item_name_lower for keyword in ["초기화 완료", "포맷 완료"]):
                        priority = 0
                    elif any(keyword in original_time_text for keyword in ["data wipe complete", "format successful"]):
                        priority = 0
                    elif any(keyword in item_name_lower for keyword in ["초기화 시작", "포맷팅"]):
                        priority = 1
                    elif any(keyword in original_time_text for keyword in ["-- wiping data", "wiping data", "formatting /data"]):
                        priority = 1
                    elif "wiping data 없음" in item_name_lower:
                        # 오래된 로그: Wiping data 없을 때 Starting recovery 시각을 보조 후보로 사용
                        priority = 2
                    elif item_name_lower in {"recovery.log", "last_log"} or "xiaomi base" in item_name_lower:
                        priority = 2

                # persistent_properties의 reboot,factory_reset 값은 리셋 관련 실제 epoch라서 다음 후보
                elif artifact_id == "4" and "persistent_properties" in item_name_lower:
                    priority = 3

                # bootstat T1 직접 아티팩트
                elif artifact_id == "1" and item_name_lower in ('factory_reset', 'factory_reset_record_value'):
                    priority = 3
                elif artifact_id == "1" and item_name_lower in ('factory_reset_current_time', 'last_boot_time_utc'):
                    priority = 5

                if priority is None:
                    continue

                candidates.append({
                    'priority': priority,
                    'display_time': display_time,
                    'time_str': time_str,
                    'artifact_name': artifact_name,
                    'item_name': item_name,
                    'path': data.get('path', '')
                })

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x['priority'], -x['display_time'].timestamp()))
        return candidates[0]

    def update_estimated_reset_time_display(self):
        """Refresh the automatically estimated reset time shown on the main screen."""
        estimate = self.get_estimated_reset_time_info()

        if not estimate:
            self.estimated_reset_time_value = None
            self.estimated_reset_time_source = None
            self.estimated_reset_time_display.setText("")
            self.estimated_reset_time_display.setToolTip("")
            self.estimated_reset_time_source_label.setText("Source: -")
            return

        self.estimated_reset_time_value = estimate['time_str']
        source_text = f"{estimate['artifact_name']} / {estimate['item_name'] or 'unknown'}"
        self.estimated_reset_time_source = source_text
        self.estimated_reset_time_display.setText(self.estimated_reset_time_value)
        self.estimated_reset_time_display.setToolTip(estimate.get('path', ''))
        self.estimated_reset_time_source_label.setText(f"Source: {source_text}")

    def _classify_time_anchor(self, artifact_id, item_name, original_time=''):
        """논문 T1/T2/T3 및 구성요소 분류"""
        name = str(item_name or '').lower()
        orig = str(original_time or '').lower()
        if artifact_id == "1":
            if name == 'factory_reset' or name == 'factory_reset_record_value':
                return 'bootstat', 'T1', 'bootstat (직접)'
            if name in ('factory_reset_current_time', 'last_boot_time_utc'):
                return 'bootstat_aux', 'T2', f'bootstat ({name})'
            if name == 'build_date':
                return 'bootstat_diag', 'diag', 'bootstat (build_date)'
        if artifact_id in {"21", "22"}:
            if any(k in name for k in ['초기화', '포맷', 'wiping']) or 'wiping' in orig:
                return 'recovery', 'T1', item_name
            if name in ('recovery.log', 'last_log') or 'get_system_time' in orig:
                return 'recovery', 'T1', item_name
            if '초기화 트리거' in name:
                return 'recovery_trigger', 'meta', item_name
        if artifact_id == "4":
            return 'persistent', 'T2', 'persistent_properties'
        if artifact_id == "7":
            return 'media', 'T2', 'internal.db (MediaProvider)'
        if artifact_id == "9":
            return 'gms', 'T2', 'ULR_PERSISTENT_PREFS'
        if artifact_id in {"3", "5", "6"}:
            label = self.artifact_names.get(artifact_id, artifact_id)
            return 'setup', 'T3', label
        if artifact_id == "8":
            return 'samsung_err', 'T1', 'eRR.p (Samsung)'
        return None, None, None

    def collect_time_anchors(self):
        """분석 결과에서 시간 앵커 수집"""
        anchors = []
        for artifact_id, data_list in self.artifact_data.items():
            if artifact_id in self.hidden_artifacts or not self.is_artifact_visible(artifact_id):
                continue
            for data in data_list:
                item_key = self._get_item_key(data)
                if item_key in self.hidden_items.get(artifact_id, set()):
                    continue
                if artifact_id == "1" and not self.is_bootstat_item_visible(data.get('name', '')):
                    continue
                time_value = data.get('time')
                if not time_value or not isinstance(time_value, datetime):
                    continue
                component, tier, label = self._classify_time_anchor(
                    artifact_id, data.get('name', ''), data.get('original_time', '')
                )
                if not component or tier == 'meta':
                    continue
                display_time, time_str = self.format_time_for_display(time_value, data.get('is_kst', False))
                anchors.append({
                    'component': component,
                    'tier': tier,
                    'label': label,
                    'item_name': data.get('name', ''),
                    'path': data.get('path', ''),
                    'time': display_time,
                    'time_str': time_str,
                    'message': data.get('message', ''),
                    'artifact_id': artifact_id,
                })
        return anchors

    def check_bootstat_co_update_pair(self):
        """factory_reset ↔ factory_reset_record_value 동시 갱신 쌍 검증 (논문 성질 1)"""
        fr_time = frv_time = build_time = None
        for data in self.artifact_data.get('1', []):
            name = str(data.get('name', '')).lower()
            tv = data.get('time')
            if not isinstance(tv, datetime):
                continue
            if name == 'factory_reset':
                fr_time = tv
            elif name == 'factory_reset_record_value':
                frv_time = tv
            elif name == 'build_date':
                build_time = tv
        if fr_time is None or frv_time is None:
            return {
                'status': 'insufficient',
                'delta_sec': None,
                'message': 'factory_reset 또는 factory_reset_record_value 중 하나 이상 없음',
                'fr_time': fr_time,
                'frv_time': frv_time,
                'build_time': build_time,
            }
        delta = abs((fr_time - frv_time).total_seconds())
        if delta <= 5:
            status = 'ok'
            msg = f'동시 갱신 쌍 일치 (|Δ|={delta:.0f}초)'
        else:
            status = 'mismatch'
            msg = f'동시 갱신 쌍 불일치 (|Δ|={delta:.0f}초) — mtime 위·변조 또는 OEM 분기 의심'
        return {
            'status': status,
            'delta_sec': delta,
            'message': msg,
            'fr_time': fr_time,
            'frv_time': frv_time,
            'build_time': build_time,
        }

    def detect_oem_anomalies(self, anchors, co_update):
        """OEM 분기 오류 M1(ROM 빌드 고정) 등 탐지"""
        anomalies = []
        fr_time = co_update.get('fr_time')
        build_time = co_update.get('build_time')
        if fr_time and build_time:
            if abs((fr_time - build_time).total_seconds()) <= 120:
                anomalies.append({
                    'code': 'M1',
                    'title': 'ROM 빌드 시각 고정 의심',
                    'detail': 'factory_reset mtime이 build_date와 일치합니다. MIUI 부류 OEM 분기 오류(M1) 가능.',
                })
        if co_update.get('status') == 'mismatch':
            anomalies.append({
                'code': 'TAMPER',
                'title': 'bootstat 동시 갱신 쌍 불일치',
                'detail': co_update.get('message', ''),
            })
        system_times = [a['time'] for a in anchors if a['tier'] in ('T1', 'T2') and a['component'] != 'bootstat_diag']
        if fr_time and system_times:
            others = [t for t in system_times if abs((t - fr_time).total_seconds()) > 86400]
            if build_time and abs((fr_time - build_time).total_seconds()) <= 120 and len(others) >= 2:
                pass  # already M1
            elif fr_time.year < 2015 and len(anchors) >= 2:
                anomalies.append({
                    'code': 'M2',
                    'title': 'epoch 미초기화/시계 폴백 의심',
                    'detail': 'bootstat 시각이 비정상적으로 과거입니다. 보조 앵커로 교차검증 필요.',
                })
        return anomalies

    def run_multi_anchor_cross_validation(self):
        """논문 Algorithm 2 — 사건 단위 다중 시간 앵커 교차검증"""
        anchors = self.collect_time_anchors()
        co_update = self.check_bootstat_co_update_pair()
        anomalies = self.detect_oem_anomalies(anchors, co_update)

        core_components = {'bootstat', 'persistent', 'gms', 'media', 'recovery'}
        system_anchors = [a for a in anchors if a['component'] in core_components]
        setup_anchors = [a for a in anchors if a['tier'] == 'T3']

        observed_components = {a['component'] for a in system_anchors}
        times = [a['time'] for a in system_anchors]
        status = 'INVESTIGATIVE-LEAD'
        status_kr = '수사 단서'
        t_reset = None
        t_reset_str = ''
        cluster_min = cluster_max = None
        cluster_width_min = None

        if len(times) >= 2:
            cluster_min = min(times)
            cluster_max = max(times)
            cluster_width_min = (cluster_max - cluster_min).total_seconds() / 60.0
            if cluster_width_min <= self.MULTI_ANCHOR_CONSISTENCY_MINUTES:
                if len(observed_components) >= 4:
                    status = 'PRIMARY'
                    status_kr = '1차 증거 (primary)'
                else:
                    status = 'CORROBORATIVE'
                    status_kr = '보강 증거 (corroborative)'
                t_reset = cluster_min
                _, t_reset_str = self.format_time_for_display(cluster_min, False)
            else:
                status_kr = '수사 단서 (앵커 분산 초과)'

        trigger_info = None
        for aid in ('21', '22'):
            for data in self.artifact_data.get(aid, []):
                if '초기화 트리거' in str(data.get('name', '')):
                    trigger_info = {
                        'type': data.get('message', ''),
                        'detail': data.get('original_time', ''),
                        'time_str': data.get('name', ''),
                    }
                    break
            if trigger_info:
                break

        result = {
            'status': status,
            'status_kr': status_kr,
            't_reset': t_reset,
            't_reset_str': t_reset_str,
            'anchors': anchors,
            'system_anchors': system_anchors,
            'setup_anchors': setup_anchors,
            'observed_components': sorted(observed_components),
            'cluster_width_min': cluster_width_min,
            'cluster_min': cluster_min,
            'cluster_max': cluster_max,
            'co_update': co_update,
            'anomalies': anomalies,
            'trigger_info': trigger_info,
            'missing_components': sorted(core_components - observed_components),
        }
        self.multi_anchor_result = result
        return result

    def update_cross_validation_display(self):
        """교차검증 UI 갱신"""
        result = self.multi_anchor_result
        if not result:
            self.cross_validation_grade_label.setText("신뢰 등급: -")
            self.cross_validation_detail_label.setText("분석 후 자동 갱신됩니다.")
            self.cross_validation_span_label.setText("시간 분포: -")
            self.cross_anchor_table.setRowCount(0)
            return
        lines = [f"신뢰 등급: {result['status_kr']}"]
        if result.get('t_reset_str'):
            lines.append(f"T_reset 추정: {result['t_reset_str']} (가장 이른 일관 앵커)")
        if result.get('cluster_width_min') is not None:
            lines.append(
                f"시스템 앵커 {len(result['system_anchors'])}개, "
                f"군집 폭 {result['cluster_width_min']:.1f}분 "
                f"(허용 {self.MULTI_ANCHOR_CONSISTENCY_MINUTES}분)"
            )
        co = result.get('co_update', {})
        if co.get('status') == 'ok':
            lines.append(f"동시 갱신 쌍: {co.get('message')}")
        elif co.get('status') == 'mismatch':
            lines.append(f"⚠ {co.get('message')}")
        if result.get('anomalies'):
            lines.append(f"OEM/이상: {result['anomalies'][0]['title']}")
        if result.get('trigger_info'):
            lines.append(f"트리거: {result['trigger_info'].get('type')}")
        if result.get('missing_components'):
            lines.append(f"누락 앵커: {', '.join(result['missing_components'])}")
        self.cross_validation_grade_label.setText(lines[0])
        self.cross_validation_detail_label.setText('\n'.join(lines[1:]))
        self.cross_validation_span_label.setText(self._build_anchor_distribution_text(result))
        self._update_cross_anchor_table(result)

    def _build_anchor_distribution_text(self, result):
        """앵커 시간 분포를 5분 버킷 텍스트로 생성"""
        anchors = list(result.get('anchors', []) or [])
        if not anchors:
            return "시간 분포: 앵커 없음"
        anchors.sort(key=lambda a: a['time'])
        base_time = anchors[0]['time']
        max_time = anchors[-1]['time']
        spread_min = (max_time - base_time).total_seconds() / 60.0

        buckets = {}
        for anchor in anchors:
            offset_min = (anchor['time'] - base_time).total_seconds() / 60.0
            bucket_index = int(offset_min // 5)
            buckets[bucket_index] = buckets.get(bucket_index, 0) + 1

        bucket_texts = []
        for bucket_index in sorted(buckets.keys())[:8]:
            start = bucket_index * 5
            end = start + 5
            bucket_texts.append(f"{start:02d}-{end:02d}분:{buckets[bucket_index]}개")
        bucket_summary = " | ".join(bucket_texts) if bucket_texts else "-"
        return f"시간 분포폭 {spread_min:.1f}분 (Earliest~Latest), 5분 버킷: {bucket_summary}"

    def _cross_component_label(self, component):
        labels = {
            "bootstat": "Bootstat",
            "bootstat_aux": "Bootstat 보조",
            "bootstat_diag": "Bootstat 진단",
            "recovery": "Recovery/LastLog",
            "recovery_trigger": "Recovery Trigger",
            "persistent": "Persistent",
            "gms": "GMS",
            "media": "MediaProvider",
            "setup": "SetupWizard",
            "samsung_err": "Samsung eRR.p",
        }
        return labels.get(component, component)

    def _cross_tier_color(self, tier):
        if tier == "T1":
            return QColor(224, 247, 232)
        if tier == "T2":
            return QColor(228, 240, 255)
        if tier == "T3":
            return QColor(255, 243, 214)
        if tier == "diag":
            return QColor(236, 236, 236)
        return QColor(245, 245, 245)

    def _update_cross_anchor_table(self, result):
        """교차검증 결과를 시간 분포 테이블로 시각화"""
        anchors = list(result.get('anchors', []) or [])
        anchors.sort(key=lambda a: (a['time'], a.get('tier', ''), a.get('label', '')))
        self.cross_anchor_table.setRowCount(len(anchors))
        if not anchors:
            return

        base_time = anchors[0]['time']
        max_offset_min = max((a['time'] - base_time).total_seconds() / 60.0 for a in anchors)
        bar_max = max(10, int(max_offset_min * 10) + 10)
        consistency_min = float(self.MULTI_ANCHOR_CONSISTENCY_MINUTES)

        for row, anchor in enumerate(anchors):
            component_text = self._cross_component_label(anchor.get('component'))
            tier_text = anchor.get('tier', '')
            time_text = anchor.get('time_str', '')
            offset_min = (anchor['time'] - base_time).total_seconds() / 60.0
            offset_text = f"+{offset_min:.1f}"

            component_item = QTableWidgetItem(component_text)
            tier_item = QTableWidgetItem(tier_text)
            time_item = QTableWidgetItem(time_text)
            offset_item = QTableWidgetItem(offset_text)

            bg = self._cross_tier_color(tier_text)
            for item in (component_item, tier_item, time_item, offset_item):
                item.setBackground(bg)

            self.cross_anchor_table.setItem(row, 0, component_item)
            self.cross_anchor_table.setItem(row, 1, tier_item)
            self.cross_anchor_table.setItem(row, 2, time_item)
            self.cross_anchor_table.setItem(row, 3, offset_item)

            bar = QProgressBar()
            bar.setRange(0, bar_max)
            bar.setValue(int(offset_min * 10))
            bar.setFormat(f"+{offset_min:.1f}분")
            bar.setTextVisible(True)
            if tier_text == "T1":
                chunk_color = "#2e7d32"
            elif tier_text == "T2":
                chunk_color = "#1565c0"
            elif tier_text == "T3":
                chunk_color = "#ef6c00"
            else:
                chunk_color = "#546e7a"
            if tier_text in ("T1", "T2") and offset_min > consistency_min:
                chunk_color = "#c62828"
            bar.setStyleSheet(
                "QProgressBar { text-align: center; }"
                f"QProgressBar::chunk {{ background-color: {chunk_color}; }}"
            )
            self.cross_anchor_table.setCellWidget(row, 4, bar)

    def show_cross_validation_report(self):
        """교차검증 상세 보고서 다이얼로그"""
        if not self.multi_anchor_result:
            self.run_multi_anchor_cross_validation()
        result = self.multi_anchor_result
        if not result or not result.get('anchors'):
            self.show_message("안내", "교차검증할 시간 앵커가 없습니다. 먼저 분석을 실행하세요.")
            return
        lines = [
            "=== 다중 시간 앵커 교차검증 보고서 ===",
            f"신뢰 등급: {result['status_kr']} ({result['status']})",
        ]
        if result.get('t_reset_str'):
            lines.append(f"T_reset 점추정: {result['t_reset_str']}")
        if result.get('cluster_width_min') is not None:
            lines.append(f"시스템 앵커 군집 폭: {result['cluster_width_min']:.1f}분")
        lines.append("")
        lines.append("[동시 갱신 쌍 — factory_reset / factory_reset_record_value]")
        co = result.get('co_update', {})
        lines.append(f"  {co.get('message', 'N/A')}")
        lines.append("")
        if result.get('trigger_info'):
            ti = result['trigger_info']
            lines.append(f"[초기화 트리거] {ti.get('type')}")
            if ti.get('detail'):
                lines.append(f"  {ti.get('detail')}")
            lines.append("")
        if result.get('anomalies'):
            lines.append("[OEM/이상 탐지]")
            for a in result['anomalies']:
                lines.append(f"  [{a['code']}] {a['title']}: {a['detail']}")
            lines.append("")
        lines.append("[시스템 앵커 T1/T2]")
        for a in result.get('system_anchors', []):
            lines.append(f"  [{a['tier']}] {a['label']}: {a['time_str']}")
        lines.append("")
        lines.append("[사용자 셋업 앵커 T3 — 보강]")
        for a in result.get('setup_anchors', []):
            lines.append(f"  {a['label']}: {a['time_str']}")
        if result.get('missing_components'):
            lines.append("")
            lines.append(f"[누락 구성요소] {', '.join(result['missing_components'])}")
        self.show_message("다중 시간 앵커 교차검증", '\n'.join(lines))

    def show_oem_artifact_matrix(self):
        """논문 표 12 — 제조사별 아티팩트 경로·가용성"""
        matrix = (
            "=== 제조사·OS 분기별 아티팩트 매트릭스 (표 12) ===\n\n"
            "아티팩트 | Samsung | Pixel | Xiaomi MIUI | HyperOS\n"
            "bootstat (/data/misc/bootstat/) | ○ | ○ | ○ | ○\n"
            "persistent_properties | ○ | ○ | ○ | ○\n"
            "internal.db (MediaProvider) | ○ | ○ | ○ | ○\n"
            "ULR_PERSISTENT_PREFS.xml | ○ | ○ | ○ | ○\n"
            "recovery.log (/data/log/) | ○ | ✗ | ✗ | ✗\n"
            "last_log (/mnt/rescue/recovery/) | ✗ | ✗ | ○ | ○\n"
            "suggestions.xml / setup_wizard | ✗ | ○ | ✗ | ✗\n"
            "appops.xml / appops_accesses.xml | 둘 다 | accesses | xml | accesses\n"
            "eRR.p (Samsung 전용) | ○ | ✗ | ✗ | ✗\n"
            "recovery_history.log (다중 이력) | ○ | ✗ | ✗ | ✗\n\n"
            "※ Pixel은 비루팅 시 recovery 로그 접근 제한\n"
            "※ Samsung은 recovery.log, Xiaomi는 last_log 사용"
        )
        self.show_message("제조사별 아티팩트 매트릭스", matrix)

    def set_confirmed_time_from_selection(self):
        """Set currently selected table cell as confirmed time"""
        table, time_col, original_col = self.get_current_result_table()
        if not table:
            self.show_message("Notice", "No selectable result table available.")
            return
        row = table.currentRow()
        if row < 0:
            self.show_message("Notice", "Please select a time to confirm.")
            return
        time_text = table.item(row, time_col).text() if table.item(row, time_col) else ""
        original_text = table.item(row, original_col).text() if table.item(row, original_col) else ""
        candidate = time_text if time_text and "없음" not in time_text else original_text
        if not candidate:
            self.show_message("Notice", "Time value is empty.")
            return
        self.confirmed_time_value = candidate
        self.confirmed_time_dt = self.parse_time_text(candidate)
        # Removed verbose confirmed time set logging
        self.update_confirmed_time_display()
        self.save_confirmed_time()
        self.apply_confirmed_time_highlight()

    def clear_confirmed_time(self):
        self.confirmed_time_value = None
        self.confirmed_time_dt = None
        self.update_confirmed_time_display()
        self.save_confirmed_time()
        self.apply_confirmed_time_highlight()

    def get_current_result_table(self):
        """현재 탭의 테이블과 시간 컬럼 인덱스 반환"""
        current_widget = self.result_tabs.currentWidget()
        if current_widget == self.summary_tab_widget:
            return self.summary_table, 3, 4
        if current_widget == self.deep_search_tab_widget:
            return None, None, None
        for artifact_id, tab in self.artifact_tab_widgets.items():
            if tab == current_widget:
                return self.artifact_tables.get(artifact_id), 2, 3
        return None, None, None

    def normalize_time_text(self, text):
        if not text:
            return ""
        return text.replace("KST", "").replace("UTC", "").strip()

    def apply_confirmed_time_highlight(self):
        """Highlight items matching confirmed time (applied only to summary results table)"""
        if not self.confirmed_time_value:
            # Remove highlighting from summary results table only if no confirmed time
            if self.summary_table:
                self.highlight_table_rows(self.summary_table, 3, 4, None)
            return
        
        target = self.normalize_time_text(self.confirmed_time_value)
        target_dt = self.confirmed_time_dt or self.parse_time_text(self.confirmed_time_value)
        if not target_dt:
            # Removed verbose highlight parsing failure logging
            if self.summary_table:
                self.highlight_table_rows(self.summary_table, 3, 4, None)
            return
        
        # Apply highlighting based on confirmed time only to summary results table
        self.highlight_table_rows(self.summary_table, 3, 4, target, target_dt)

    def clear_all_highlight(self):
        """Remove all highlighting (used when confirmed time changes)"""
        if self.summary_table:
            self.highlight_table_rows(self.summary_table, 3, 4, None)
        # Re-highlight artifact tables based on their respective times
        for artifact_id, table in self.artifact_tables.items():
            if artifact_id in self.artifact_data:
                data_list = self.artifact_data[artifact_id]
                self.highlight_artifact_table(artifact_id, table, data_list)

    def highlight_table_rows(self, table, time_col, original_col, target, target_dt=None):
        if not table:
            return
        for row in range(table.rowCount()):
            time_text = table.item(row, time_col).text() if table.item(row, time_col) else ""
            orig_text = table.item(row, original_col).text() if table.item(row, original_col) else ""
            row_item = table.item(row, 0).text().lower() if table.item(row, 0) else ""
            row_path = table.item(row, 1).text().lower() if table.item(row, 1) else ""
            is_recovery_row = ("recovery" in row_item) or ("recovery" in row_path)
            time_norm = self.normalize_time_text(time_text)
            orig_norm = self.normalize_time_text(orig_text)
            match = False
            if target_dt:
                time_dt = self.parse_time_text(time_text) or self.parse_time_text(orig_text)
                if time_dt:
                    # recovery.log 등은 UTC로 기록되는 경우가 있어 ±9시간 허용
                    diffs = [
                        abs((time_dt - target_dt).total_seconds()),
                        abs((time_dt + timedelta(hours=9) - target_dt).total_seconds()),
                        abs((time_dt - timedelta(hours=9) - target_dt).total_seconds()),
                    ]
                    match = min(diffs) <= 60
                else:
                    pass  # Removed verbose highlight debug logging
            if not match and target:
                match = (target in time_norm) or (target in orig_norm) or (time_norm in target) or (orig_norm in target)
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if not item:
                    continue
                if match:
                    item.setBackground(Qt.yellow)
                else:
                    item.setBackground(Qt.white)

    def parse_time_text(self, text):
        if not text:
            return None
        raw = text.replace("KST", "").replace("UTC", "").strip()

        # Extract recovery.log original format (Fri Dec  6 05:37:34 2024)
        rec_match = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\b", raw)
        if rec_match:
            rec_str = " ".join(rec_match.group(0).split())
            try:
                return datetime.strptime(rec_str, "%a %b %d %H:%M:%S %Y")
            except Exception:
                pass

        # Extract date/time pattern from string first
        dt_match = re.search(r"\d{4}[-/\.]\d{2}[-/\.]\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+\d{4}|\+\d{2}:\d{2})?", raw)
        if dt_match:
            raw = dt_match.group(0)

        # Handle timezone-included pattern
        if re.search(r"\+\d{4}$", raw):
            try:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S%z").replace(tzinfo=None)
            except Exception:
                pass
        if re.search(r"\+\d{2}:\d{2}$", raw):
            try:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S%z").replace(tzinfo=None)
            except Exception:
                pass

        # recovery.log 원문 포맷 (Fri Dec  6 05:37:34 2024)
        try:
            alt = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
            return alt
        except Exception:
            pass

        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        return None
    
    def get_selected_source(self):
        """Return selected search target"""
        if self.radio_zip.isChecked():
            return "1"
        elif self.radio_adb.isChecked():
            return "2"
        elif self.radio_folder.isChecked():
            return "3"
        return None
    
    def get_selected_artifacts(self):
        """Return selected artifact list"""
        if self.checkbox_all.isChecked():
            return "0"
        
        selected_set = set()
        for checkbox, value in self.checkbox_to_artifact_id.items():
            if checkbox.isChecked():
                selected_set.add(value)

        if not selected_set:
            return ["0"]
        ordered = [aid for aid in self.artifact_names.keys() if aid in selected_set]
        return ordered
    
    def on_timezone_changed(self, state):
        """Update tables when timezone changes"""
        self.use_kst = (state == Qt.Checked)
        self.update_all_tables()
    
    def update_all_tables(self):
        """Update all tables according to timezone"""
        for artifact_id, table in self.artifact_tables.items():
            if artifact_id in self.artifact_data:
                self.update_table(artifact_id, self.artifact_data[artifact_id])
        
        # 필터링 적용
        self.apply_artifact_filter()
        
        # Update summary results tab as well
        self.update_summary_table()
        self.run_multi_anchor_cross_validation()
        self.update_cross_validation_display()
    
    def update_table(self, artifact_id, data_list):
        """Update table for specific artifact"""
        table = self.artifact_tables.get(artifact_id)
        if not table:
            return
        
        table.setRowCount(0)
        
        # Check if data exists (if there are items with time information)
        has_time_data = False
        for data in data_list:
            if data.get('time'):
                has_time_data = True
                break
        
        # Update tab name (including status info) - only if not hidden and visible by filter
        if artifact_id not in self.hidden_artifacts and self.is_artifact_visible(artifact_id):
            base_name = self.artifact_names.get(artifact_id, artifact_id)
            if has_time_data:
                tab_name = f"✓ {base_name}"
            elif data_list:
                tab_name = f"✗ {base_name} (No Data)"
            else:
                # Detailed status information while waiting
                if not self.analysis_running:
                    # Before analysis
                    status = "Before Analysis"
                elif artifact_id not in self.selected_artifacts and "0" not in self.selected_artifacts:
                    # Not selected
                    status = "Not Selected"
                elif self.analysis_running:
                    # Analyzing
                    status = "Analyzing"
                else:
                    # Analysis completed but no data
                    status = "No Data"
                tab_name = f"{base_name} ({status})"
            
            # Find tab index
            for i in range(self.result_tabs.count()):
                widget = self.result_tabs.widget(i)
                if widget == self.artifact_tab_widgets.get(artifact_id):
                    self.result_tabs.setTabText(i, tab_name)
                    break
        
        # Display status information in table even when there is no data
        if not data_list:
            table.insertRow(0)
            # Checkbox column (empty for status row)
            checkbox_item = QTableWidgetItem("")
            checkbox_item.setFlags(Qt.NoItemFlags)
            table.setItem(0, 0, checkbox_item)
            
            item_name = QTableWidgetItem("Status")
            table.setItem(0, 1, item_name)
            
            item_path = QTableWidgetItem("")
            table.setItem(0, 2, item_path)
            
            # Status message
            if not self.analysis_running:
                status_msg = "Analysis has not been run yet."
            elif artifact_id not in self.selected_artifacts and "0" not in self.selected_artifacts:
                status_msg = "This artifact was not selected."
            elif self.analysis_running:
                status_msg = "Analysis is in progress. Please wait..."
            else:
                status_msg = "Analysis completed but no data found."
            
            item_time = QTableWidgetItem(status_msg)
            table.setItem(0, 3, item_time)
            
            item_original = QTableWidgetItem("")
            table.setItem(0, 4, item_original)
        
        # Filter out hidden items
        visible_data_list = []
        for data in data_list:
            item_key = self._get_item_key(data)
            hidden_items = self.hidden_items.get(artifact_id, set())
            if item_key in hidden_items:
                continue
            if artifact_id == "1" and not self.is_bootstat_item_visible(data.get('name', '')):
                continue
            visible_data_list.append(data)
        
        # Block signals while updating table to prevent recursion
        table.blockSignals(True)
        try:
            for row, data in enumerate(visible_data_list):
                table.insertRow(row)
                
                # Create item key for this row
                item_key = self._get_item_key(data)
                hidden_items = self.hidden_items.get(artifact_id, set())
                is_hidden = item_key in hidden_items
                
                # Checkbox column
                checkbox_item = QTableWidgetItem()
                checkbox_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                checkbox_item.setCheckState(Qt.Unchecked if is_hidden else Qt.Checked)
                checkbox_item.setData(Qt.UserRole, item_key)  # Store item_key for reference
                table.setItem(row, 0, checkbox_item)
                
                # 항목
                item_name = QTableWidgetItem(data.get('name', ''))
                table.setItem(row, 1, item_name)
                
                # 경로
                item_path = QTableWidgetItem(data.get('path', ''))
                table.setItem(row, 2, item_path)
                
                # Time (converted according to timezone)
                time_value = data.get('time')
                is_kst = data.get('is_kst', False)  # Check if already KST
                
                if time_value:
                    if isinstance(time_value, datetime):
                        if is_kst:
                            # If already KST, don't convert
                            if self.use_kst:
                                time_str = time_value.strftime('%Y-%m-%d %H:%M:%S KST')
                            else:
                                # Subtract 9 hours to display as UTC
                                display_time = time_value - timedelta(hours=9)
                                time_str = display_time.strftime('%Y-%m-%d %H:%M:%S UTC')
                        else:
                            # UTC인 경우
                            if self.use_kst:
                                display_time = time_value + timedelta(hours=9)
                                time_str = display_time.strftime('%Y-%m-%d %H:%M:%S KST')
                            else:
                                display_time = time_value
                                time_str = display_time.strftime('%Y-%m-%d %H:%M:%S UTC')
                    else:
                        time_str = str(time_value)
                else:
                    time_str = data.get('message', 'No time information')
                
                item_time = QTableWidgetItem(time_str)
                table.setItem(row, 3, item_time)
                
                # Display original time
                original_time = data.get('original_time')
                if original_time:
                    if isinstance(original_time, datetime):
                        original_time_str = original_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        original_time_str = str(original_time)
                else:
                    original_time_str = time_str if time_value else ''
                
                item_original = QTableWidgetItem(original_time_str)
                table.setItem(row, 4, item_original)
        finally:
            # Unblock signals after updating table
            table.blockSignals(False)
        
        table.resizeColumnsToContents()
        # Each artifact table is highlighted based on that artifact's time data
        self.highlight_artifact_table(artifact_id, table, visible_data_list)
    
    def highlight_artifact_table(self, artifact_id, table, data_list):
        """Highlight based on time extracted from each artifact table"""
        if not table or not data_list:
            return
        
        # Block signals during highlighting to prevent recursion
        table.blockSignals(True)
        try:
            # Collect all times extracted from that artifact
            extracted_times = []
            for data in data_list:
                time_value = data.get('time')
                if time_value and isinstance(time_value, datetime):
                    extracted_times.append(time_value)
            
            if not extracted_times:
                # Don't highlight if no time
                return
            
            # Compare with each row's time and highlight
            for row in range(table.rowCount()):
                time_item = table.item(row, 2)
                orig_item = table.item(row, 3)
                
                if not time_item:
                    continue
                
                time_text = time_item.text()
                orig_text = orig_item.text() if orig_item else ""
                
                # Parse current row's time
                row_time_dt = self.parse_time_text(time_text) or self.parse_time_text(orig_text)
                
                # For persistent_properties, original time may be in special format, so compare directly
                if not row_time_dt and artifact_id == "4" and orig_text:
                    # Try to extract epoch value from original time
                    epoch_match = re.search(r'(\d{10})', orig_text)
                    if epoch_match:
                        try:
                            epoch_value = int(epoch_match.group(1))
                            if epoch_value > 253402300799:
                                epoch_value /= 1000
                            row_time_dt = datetime.utcfromtimestamp(epoch_value)
                        except (ValueError, OverflowError):
                            pass
                
                if not row_time_dt:
                    continue
                
                # Compare with extracted times (±1 minute allowed)
                match = False
                for extracted_dt in extracted_times:
                    # Allow ±9 hours considering UTC/KST difference
                    diffs = [
                        abs((row_time_dt - extracted_dt).total_seconds()),
                        abs((row_time_dt + timedelta(hours=9) - extracted_dt).total_seconds()),
                        abs((row_time_dt - timedelta(hours=9) - extracted_dt).total_seconds()),
                    ]
                    if min(diffs) <= 60:  # Difference within 1 minute
                        match = True
                        break
                
                # 하이라이팅 적용
                for col in range(table.columnCount()):
                    item = table.item(row, col)
                    if item:
                        if match:
                            item.setBackground(Qt.yellow)
                        else:
                            item.setBackground(Qt.white)
        finally:
            table.blockSignals(False)
    
    def update_summary_table(self):
        """Update summary results tab - display all artifacts' time information sorted by time"""
        if not self.summary_table:
            return
        
        self.summary_table.setRowCount(0)
        
        # Collect data with time information from all artifacts
        all_time_data = []
        
        for artifact_id, data_list in self.artifact_data.items():
            # Exclude hidden artifacts from summary results
            if artifact_id in self.hidden_artifacts:
                continue
            
            # Filtering: show only artifacts selected by checkbox
            if not self.is_artifact_visible(artifact_id):
                continue
                
            artifact_name = self.artifact_names.get(artifact_id, artifact_id)
            
            for data in data_list:
                # Filter out hidden items
                item_key = self._get_item_key(data)
                hidden_items = self.hidden_items.get(artifact_id, set())
                if item_key in hidden_items:
                    continue
                if artifact_id == "1" and not self.is_bootstat_item_visible(data.get('name', '')):
                    continue
                
                time_value = data.get('time')
                if time_value and isinstance(time_value, datetime):
                    display_time, time_str = self.format_time_for_display(
                        time_value,
                        data.get('is_kst', False)
                    )
                    
                    # Original time data
                    original_time = data.get('original_time')
                    if original_time:
                        if isinstance(original_time, datetime):
                            original_time_str = original_time.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            original_time_str = str(original_time)
                    else:
                        original_time_str = time_str
                    
                    all_time_data.append({
                        'time': display_time,
                        'artifact_name': artifact_name,
                        'name': data.get('name', ''),
                        'path': data.get('path', ''),
                        'time_str': time_str,
                        'original_time_str': original_time_str
                    })
        
        # Sort by time
        all_time_data.sort(key=lambda x: x['time'])
        
        # Add to table
        for row, data in enumerate(all_time_data):
            self.summary_table.insertRow(row)
            
            # Artifact name
            item_artifact = QTableWidgetItem(data['artifact_name'])
            self.summary_table.setItem(row, 0, item_artifact)
            
            # Item name
            item_name = QTableWidgetItem(data['name'])
            self.summary_table.setItem(row, 1, item_name)
            
            # 경로
            item_path = QTableWidgetItem(data['path'])
            self.summary_table.setItem(row, 2, item_path)
            
            # 시간
            item_time = QTableWidgetItem(data['time_str'])
            # Store datetime object for sorting (convert to number)
            item_time.setData(Qt.UserRole, data['time'].timestamp())
            self.summary_table.setItem(row, 3, item_time)
            
            # Original time
            item_original = QTableWidgetItem(data['original_time_str'])
            self.summary_table.setItem(row, 4, item_original)
        
        self.summary_table.resizeColumnsToContents()
        self.update_estimated_reset_time_display()
        self.apply_confirmed_time_highlight()
        
        # Sort by time column (ascending order)
        if all_time_data:
            self.summary_table.sortItems(3, Qt.AscendingOrder)
    
    def reorder_tabs(self):
        """Reorder tabs: display tabs with data first"""
        # Check status of each artifact
        tab_states = []  # (artifact_id, has_data, has_time, base_name)
        
        for artifact_id, base_name in self.artifact_names.items():
            has_data = artifact_id in self.artifact_data and len(self.artifact_data[artifact_id]) > 0
            has_time = False
            if has_data:
                for data in self.artifact_data[artifact_id]:
                    if data.get('time'):
                        has_time = True
                        break
            
            tab_states.append((artifact_id, has_data, has_time, base_name))
        
        # Sort: tabs with time data > tabs with data but no time > tabs with no data
        tab_states.sort(key=lambda x: (not x[2], not x[1]))
        
        # Store currently selected tab index
        current_index = self.result_tabs.currentIndex()
        current_widget = self.result_tabs.currentWidget() if current_index >= 0 else None
        
        # Store summary results tab and deep search results tab widgets
        summary_widget = None
        deep_search_widget = None
        summary_index = -1
        deep_search_index = -1
        
        # Find tab widgets
        for i in range(self.result_tabs.count()):
            tab_text = self.result_tabs.tabText(i)
            if "Summary Results" in tab_text or tab_text.startswith("✓ Summary"):
                summary_widget = self.result_tabs.widget(i)
                summary_index = i if current_index == i else -1
            elif "Deep Search Results" in tab_text:
                deep_search_widget = self.result_tabs.widget(i)
                deep_search_index = i if current_index == i else -1
        
        # 모든 탭 제거 (위젯은 유지)
        while self.result_tabs.count() > 0:
            self.result_tabs.removeTab(0)
        
        # Summary results tab added first
        new_current_index = -1
        if summary_widget:
            self.result_tabs.addTab(summary_widget, "✓ Summary Results")
            if summary_index >= 0:
                new_current_index = 0
        
        # Deep search results tab added (second)
        if deep_search_widget:
            self.result_tabs.addTab(deep_search_widget, "Deep Search Results")
            if deep_search_index >= 0:
                new_current_index = 1
        
        # 정렬된 순서대로 탭 다시 추가
        start_idx = 1
        if summary_widget:
            start_idx += 1
        if deep_search_widget:
            start_idx += 1
        for idx, (artifact_id, has_data, has_time, base_name) in enumerate(tab_states):
            tab_widget = self.artifact_tab_widgets[artifact_id]
            
            # Determine tab name
            if has_time:
                tab_name = f"✓ {base_name}"
            elif has_data:
                tab_name = f"✗ {base_name} (No Data)"
            else:
                # Detailed status information while waiting
                if artifact_id not in self.selected_artifacts and "0" not in self.selected_artifacts:
                    status = "Not Selected"
                else:
                    status = "No Data"
                tab_name = f"{base_name} ({status})"
            
            self.result_tabs.addTab(tab_widget, tab_name)
            
            # 이전에 선택된 탭이면 인덱스 저장
            if current_widget and tab_widget == current_widget:
                new_current_index = start_idx + idx
        
        # 이전 선택 복원
        if new_current_index >= 0:
            self.result_tabs.setCurrentIndex(new_current_index)
        elif summary_index == 0:
            self.result_tabs.setCurrentIndex(0)
    
    def add_artifact_data(self, artifact_id, name, path, time_value=None, message=None, is_kst=False, original_time=None):
        """Thread-safe entrypoint for artifact row updates."""
        if QThread.currentThread() != self.thread():
            self.add_artifact_data_signal.emit(
                artifact_id, name, path, time_value, message, is_kst, original_time
            )
            return
        self._add_artifact_data_ui(artifact_id, name, path, time_value, message, is_kst, original_time)

    def _add_artifact_data_ui(self, artifact_id, name, path, time_value=None, message=None, is_kst=False, original_time=None):
        """아티팩트 데이터 추가
        Args:
            artifact_id: 아티팩트 ID
            name: 항목 이름
            path: 파일 경로
            time_value: 시간 값 (datetime 객체 또는 None)
            message: 메시지 (시간이 없을 때 표시)
            is_kst: 이미 KST 시간인지 여부 (True면 UTC 변환 안 함)
            original_time: 원본 시간 데이터 (정규화 전)
        """
        if artifact_id not in self.artifact_data:
            self.artifact_data[artifact_id] = []
        
        self.artifact_data[artifact_id].append({
            'name': name,
            'path': path,
            'time': time_value,
            'message': message,
            'is_kst': is_kst,  # 이미 KST인 경우 플래그
            'original_time': original_time  # 원본 시간 데이터
        })
        
        # 표 업데이트
        self.update_table(artifact_id, self.artifact_data[artifact_id])
        
        # 종합 결과 탭 업데이트
        self.update_summary_table()
    
    def clear_artifact_data(self, artifact_id):
        """아티팩트 데이터 초기화"""
        if artifact_id in self.artifact_data:
            self.artifact_data[artifact_id] = []
        table = self.artifact_tables.get(artifact_id)
        if table:
            table.setRowCount(0)
    
    def run_analysis(self):
        """분석 실행"""
        # 입력 검증
        source = self.get_selected_source()
        if not source:
            self.show_message("경고", "검색 대상을 선택하세요.")
            return
        
        if source in ["1", "3"]:
            file_path = self.file_path_edit.text()
            if not file_path:
                self.show_message("경고", "파일 또는 폴더를 선택하세요.")
                return
        
        artifacts = self.get_selected_artifacts()
        if not artifacts:
            self.show_message("경고", "최소 하나의 아티팩트를 선택하세요.")
            return
        
        # 선택된 아티팩트 저장
        self.selected_artifacts = artifacts if isinstance(artifacts, list) else [artifacts]
        self.analysis_running = True
        
        # 이전 분석 인스턴스 정리
        if hasattr(self, 'worker_thread') and self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.terminate()
            self.worker_thread.wait()
        if hasattr(self, 'deep_search_thread') and self.deep_search_thread and self.deep_search_thread.isRunning():
            self.deep_search_thread.terminate()
            self.deep_search_thread.wait()
        
        self.reset_instance = None
        
        # 데이터 초기화
        self.artifact_data = {}
        for artifact_id in self.artifact_tables.keys():
            self.clear_artifact_data(artifact_id)
            # Update all tabs to "Analyzing" or "Not Selected" status
            self.update_table(artifact_id, [])
        
        # Initialize summary results tab
        if self.summary_table:
            self.summary_table.setRowCount(0)
        
        self.result_text.clear()
        self.result_text.append("=" * 60)
        self.result_text.append("Factory Reset Artifact Analysis 시작")
        self.result_text.append("=" * 60 + "\n")
        
        # 진행 표시줄 표시
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 무한 진행
        self.btn_run.setEnabled(False)
        
        # ResetClass 인스턴스 생성 및 설정
        self.reset_instance = ResetClassGUI(source, artifacts, self.file_path_edit.text(), 
                                            self.result_text, self)
        
        # 백그라운드 스레드에서 실행
        self.worker_thread = WorkerThread(self.reset_instance)
        self.worker_thread.output.connect(self.result_text.append)
        self.worker_thread.finished.connect(self.analysis_finished)
        self.worker_thread.start()
    
    def analysis_finished(self):
        """분석 완료 처리"""
        self.analysis_running = False
        self.progress_bar.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_deep_search.setEnabled(True)  # Enable deep search button
        self.result_text.append("\n" + "=" * 60)
        self.result_text.append("분석 완료")
        self.result_text.append("=" * 60)
        
        # 모든 탭 상태 업데이트 (분석 완료 후 상태 반영)
        for artifact_id in self.artifact_tables.keys():
            data_list = self.artifact_data.get(artifact_id, [])
            self.update_table(artifact_id, data_list)
        
        # 탭 순서 재정렬 (데이터가 있는 탭을 먼저)
        self.reorder_tabs()

        # 논문 §3.4 다중 시간 앵커 교차검증
        self.run_multi_anchor_cross_validation()
        self.update_cross_validation_display()
        self.update_estimated_reset_time_display()
        
        # 분석 결과 자동 저장
        self.save_analysis_result()
    
    def run_deep_search(self):
        """Execute deep search - search files using extracted times"""
        if not self.reset_instance:
            self.show_message("경고", "먼저 분석을 실행하세요.")
            return
        
        # 추출된 시간 정보 수집
        search_times = []
        for artifact_id, data_list in self.artifact_data.items():
            for data in data_list:
                time_value = data.get('time')
                if time_value and isinstance(time_value, datetime):
                    is_kst = data.get('is_kst', False)
                    # UTC로 변환 (검색용)
                    if is_kst:
                        utc_time = time_value - timedelta(hours=9)
                    else:
                        utc_time = time_value
                    search_times.append({
                        'time': utc_time,
                        'original_time': data.get('original_time'),
                        'artifact_id': artifact_id,
                        'name': data.get('name', ''),
                        'path': data.get('path', '')
                    })
        
        if not search_times:
            self.show_message("정보", "검색할 시간 정보가 없습니다.")
            return
        
        # Initialize deep search results tab
        if self.deep_search_table:
            self.deep_search_table.setRowCount(0)
        
        # 진행 표시줄 표시
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 무한 진행 (파일 수를 알 때까지)
        self.progress_bar.setFormat("Preparing Deep Search...")
        self.btn_deep_search.setEnabled(False)
        
        # 백그라운드에서 검색 실행 (시간 오차 허용 범위: 300초 = 5분)
        time_tolerance = 300  # 5분 오차 허용
        self.deep_search_thread = DeepSearchThread(self.reset_instance, search_times, self, time_tolerance)
        self.deep_search_thread.result_found.connect(self.add_deep_search_result)
        self.deep_search_thread.progress_updated.connect(self.update_deep_search_progress)
        self.deep_search_thread.finished.connect(self.deep_search_finished)
        self.deep_search_thread.start()
    
    def add_deep_search_result(self, search_time_str, file_path, match_format, match_value):
        """Add deep search result"""
        if not self.deep_search_table:
            return
        
        row = self.deep_search_table.rowCount()
        self.deep_search_table.insertRow(row)
        
        self.deep_search_table.setItem(row, 0, QTableWidgetItem(search_time_str))
        self.deep_search_table.setItem(row, 1, QTableWidgetItem(file_path))
        self.deep_search_table.setItem(row, 2, QTableWidgetItem(match_format))
        match_item = QTableWidgetItem(str(match_value))
        # "시간 없음" 표시가 붙은 경우 원본 매칭값을 저장
        raw_match_value = str(match_value).replace(" (시간 없음)", "")
        if str(match_format).startswith("hex_") or str(match_format) == "file_mtime":
            raw_match_value = ""
        match_item.setData(Qt.UserRole, raw_match_value)
        self.deep_search_table.setItem(row, 3, match_item)
        
        self.deep_search_table.resizeColumnsToContents()

    def show_deep_search_detail(self, row, column):
        """View deep search result details"""
        import sys
        try:
            print(f"[DEBUG] show_deep_search_detail called: row={row}, column={column}", file=sys.stderr)
            sys.stderr.flush()
            
            if not self.deep_search_table:
                print("[DEBUG] No deep_search_table", file=sys.stderr)
                sys.stderr.flush()
                return
            
            # Check if row is valid
            if row < 0 or row >= self.deep_search_table.rowCount():
                print(f"[DEBUG] Invalid row: {row}, table rowCount: {self.deep_search_table.rowCount()}", file=sys.stderr)
                sys.stderr.flush()
                return

            def get_text(col):
                try:
                    item = self.deep_search_table.item(row, col)
                    return item.text() if item else ""
                except:
                    return ""

            search_time = get_text(0)
            file_path = get_text(1)
            match_format = get_text(2)
            match_item = self.deep_search_table.item(row, 3)
            match_value = get_text(3)
            raw_match_value = match_item.data(Qt.UserRole) if match_item else match_value

            print(f"[DEBUG] Extracted: search_time={search_time}, file_path={file_path}, match_format={match_format}", file=sys.stderr)
            sys.stderr.flush()

            if not any([search_time, file_path, match_format, match_value]):
                print("[DEBUG] No data found, skipping", file=sys.stderr)
                sys.stderr.flush()
                return
            
            # Skip if no file path
            if not file_path or file_path.strip() == "":
                print("[DEBUG] No file path, skipping", file=sys.stderr)
                sys.stderr.flush()
                return
            # Delegate to a value-based implementation to avoid QTableWidgetItem lifetime issues
            self._show_deep_search_detail_from_values(
                search_time=search_time,
                file_path=file_path,
                match_format=match_format,
                match_value=match_value,
                raw_match_value=raw_match_value,
            )
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = f"[ERROR] Exception in show_deep_search_detail: {e}\n{error_trace}"
            print(error_msg, file=sys.stderr)
            sys.stderr.flush()
            try:
                if hasattr(self, 'log'):
                    self.log(f"[ERROR] Exception in show_deep_search_detail: {e}")
                    self.log(error_trace)
            except:
                pass

    def _show_deep_search_detail_from_values(self, search_time, file_path, match_format, match_value, raw_match_value):
        """Value-based deep search detail viewer (avoids QTableWidgetItem lifetime/sorting issues)."""
        import sys
        try:
            print(
                f"[DEBUG] _show_deep_search_detail_from_values: file_path={file_path}, match_format={match_format}",
                file=sys.stderr,
            )
            sys.stderr.flush()

            print(f"[DEBUG] Calling get_deep_search_raw_data with file_path: {file_path}", file=sys.stderr)
            sys.stderr.flush()
            raw_info, raw_error = self.get_deep_search_raw_data(file_path, raw_match_value)

            dialog = QDialog(self)
            dialog.setWindowTitle("Deep Search Details")
            dialog_layout = QVBoxLayout()
            dialog.setLayout(dialog_layout)

            header = QTextEdit()
            header.setReadOnly(True)
            header.setPlainText(
                f"검색 시간: {search_time}\n"
                f"파일 경로: {file_path}\n"
                f"매칭 형식: {match_format}\n"
                f"매칭 값: {match_value}"
            )
            header.setFontFamily("Courier")
            header.setFixedHeight(90)
            dialog_layout.addWidget(header)

            tabs = QTabWidget()
            dialog_layout.addWidget(tabs)

            raw_text = QTextEdit()
            raw_text.setReadOnly(True)
            raw_text.setFontFamily("Courier")

            hex_text = QTextEdit()
            hex_text.setReadOnly(True)
            hex_text.setFontFamily("Courier")

            if raw_error:
                raw_text.setPlainText(f"원문 데이터: {raw_error}")
                hex_text.setPlainText(f"HEX 데이터: {raw_error}")
            else:
                raw_text.setPlainText(
                    f"원문 데이터 (라인 {raw_info['line_no']}):\n"
                    f"{raw_info['snippet']}"
                )
                hex_view = self.format_hex_view(
                    raw_info['raw_bytes'],
                    raw_info.get('byte_offset'),
                    raw_info.get('encoding'),
                    show_full=True
                )
                hex_text.setPlainText(hex_view)

            tabs.addTab(raw_text, "원문")
            tabs.addTab(hex_text, "HEX/디코딩")

            dialog.resize(800, 600)
            dialog.exec_()
            print("[DEBUG] _show_deep_search_detail_from_values completed", file=sys.stderr)
            sys.stderr.flush()
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = f"[ERROR] Exception in _show_deep_search_detail_from_values: {e}\n{error_trace}"
            print(error_msg, file=sys.stderr)
            sys.stderr.flush()
            try:
                if hasattr(self, 'show_message'):
                    self.show_message("Error", f"Error showing deep search detail: {str(e)}")
            except Exception:
                pass
            try:
                if hasattr(self, 'show_message'):
                    self.show_message("Error", f"Error showing deep search detail: {str(e)}")
            except:
                pass

    def show_summary_detail(self, row, column):
        """View summary results details (original/HEX)"""
        import sys
        try:
            print(f"[DEBUG] show_summary_detail called: row={row}, column={column}", file=sys.stderr)
            sys.stderr.flush()
            
            if not self.summary_table:
                print("[DEBUG] No summary_table", file=sys.stderr)
                sys.stderr.flush()
                return
            
            # Check if row is valid
            if row < 0 or row >= self.summary_table.rowCount():
                print(f"[DEBUG] Invalid row: {row}, table rowCount: {self.summary_table.rowCount()}", file=sys.stderr)
                sys.stderr.flush()
                return

            def get_text(col):
                try:
                    item = self.summary_table.item(row, col)
                    return item.text() if item else ""
                except:
                    return ""

            artifact_name = get_text(0)
            item_name = get_text(1)
            file_path = get_text(2)
            time_value = get_text(3)
            original_time = get_text(4)
            
            print(f"[DEBUG] Extracted: artifact_name={artifact_name}, item_name={item_name}, file_path={file_path}", file=sys.stderr)
            sys.stderr.flush()
            
            # Skip if no file path
            if not file_path or file_path.strip() == "":
                print("[DEBUG] No file path, skipping", file=sys.stderr)
                sys.stderr.flush()
                return

            match_hint = original_time or time_value
            header_text = (
                f"아티팩트: {artifact_name}\n"
                f"항목: {item_name}\n"
                f"파일 경로: {file_path}\n"
                f"시간: {time_value}\n"
                f"원본 시간: {original_time}"
            )
            
            print(f"[DEBUG] Calling show_raw_hex_dialog with file_path: {file_path}", file=sys.stderr)
            sys.stderr.flush()
            self.show_raw_hex_dialog("Summary Results Details", header_text, file_path, match_hint, context_item_name=item_name)
            print("[DEBUG] show_raw_hex_dialog completed", file=sys.stderr)
            sys.stderr.flush()
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = f"[ERROR] Exception in show_summary_detail: {e}\n{error_trace}"
            print(error_msg, file=sys.stderr)
            sys.stderr.flush()
            try:
                if hasattr(self, 'log'):
                    self.log(f"[ERROR] Exception in show_summary_detail: {e}")
                    self.log(error_trace)
            except:
                pass
            try:
                if hasattr(self, 'show_message'):
                    self.show_message("Error", f"Error showing summary detail: {str(e)}")
            except:
                pass

    def show_tab_context_menu(self, position):
        """탭 우클릭 메뉴 표시"""
        tab_index = self.result_tabs.tabBar().tabAt(position)
        if tab_index < 0:
            return
        
        # Check if artifact tab (excluding deep search results tab)
        tab_widget = self.result_tabs.widget(tab_index)
        artifact_id = None
        for aid, widget in self.artifact_tab_widgets.items():
            if widget == tab_widget:
                artifact_id = aid
                break
        
        if artifact_id is None:
            return
        
        artifact_name = self.artifact_names.get(artifact_id, artifact_id)
        is_hidden = artifact_id in self.hidden_artifacts
        
        menu = QMenu(self)
        if is_hidden:
            show_action = menu.addAction("표시하기")
            show_action.triggered.connect(lambda: self.show_artifact(artifact_id))
        else:
            hide_action = menu.addAction("숨기기")
            hide_action.triggered.connect(lambda: self.hide_artifact(artifact_id))
        
        menu.exec_(self.result_tabs.tabBar().mapToGlobal(position))
    
    def hide_artifact(self, artifact_id):
        """아티팩트 숨기기"""
        if artifact_id not in self.hidden_artifacts:
            self.hidden_artifacts.add(artifact_id)
            # 탭 숨기기
            tab_widget = self.artifact_tab_widgets.get(artifact_id)
            if tab_widget:
                tab_index = self.result_tabs.indexOf(tab_widget)
                if tab_index >= 0:
                    self.result_tabs.removeTab(tab_index)
            # Update summary results
            self.update_summary_table()
    
    def show_artifact(self, artifact_id):
        """아티팩트 표시하기"""
        if artifact_id in self.hidden_artifacts:
            self.hidden_artifacts.remove(artifact_id)
            # 탭 다시 추가
            tab_widget = self.artifact_tab_widgets.get(artifact_id)
            if tab_widget:
                artifact_name = self.artifact_names.get(artifact_id, artifact_id)
                # 이미 탭이 있는지 확인
                if self.result_tabs.indexOf(tab_widget) < 0:
                    # 적절한 위치에 탭 추가 (다른 아티팩트 탭들 사이에)
                    insert_index = len(self.artifact_tab_widgets)
                    for i in range(self.result_tabs.count()):
                        widget = self.result_tabs.widget(i)
                        if widget in self.artifact_tab_widgets.values():
                            insert_index = i + 1
                        break
                    self.result_tabs.insertTab(insert_index, tab_widget, artifact_name)
            # Update summary results
            self.update_summary_table()

    def _get_item_key(self, data):
        """Generate unique key for an item"""
        path = data.get('path', '')
        name = data.get('name', '')
        return f"{path}|{name}"
    
    def on_item_visibility_changed(self, artifact_id, item_key, state):
        """Handle checkbox state change for item visibility"""
        if artifact_id not in self.hidden_items:
            self.hidden_items[artifact_id] = set()
        
        if state == Qt.Unchecked:
            # Hide item
            self.hidden_items[artifact_id].add(item_key)
        else:
            # Show item
            self.hidden_items[artifact_id].discard(item_key)
        
        # Refresh table to apply changes (block signals to prevent recursion)
        if artifact_id in self.artifact_data:
            table = self.artifact_tables.get(artifact_id)
            if table:
                # Block signals before updating to prevent infinite recursion
                table.blockSignals(True)
                try:
                    self.update_table(artifact_id, self.artifact_data[artifact_id])
                finally:
                    table.blockSignals(False)
        # Update summary table
        self.update_summary_table()
    
    def show_table_row_context_menu(self, position, table, artifact_id):
        """Show context menu for table row"""
        row = table.rowAt(position.y())
        if row < 0:
            return
        
        item_key_item = table.item(row, 0)  # Checkbox item
        if not item_key_item:
            return
        
        item_key = item_key_item.data(Qt.UserRole)
        if not item_key:
            return
        
        hidden_items = self.hidden_items.get(artifact_id, set())
        is_hidden = item_key in hidden_items
        
        menu = QMenu(self)
        if is_hidden:
            show_action = menu.addAction("Show Item")
            show_action.triggered.connect(lambda: self.toggle_item_visibility(artifact_id, item_key, True))
        else:
            hide_action = menu.addAction("Hide Item")
            hide_action.triggered.connect(lambda: self.toggle_item_visibility(artifact_id, item_key, False))
        
        menu.exec_(table.viewport().mapToGlobal(position))
    
    def toggle_item_visibility(self, artifact_id, item_key, show):
        """Toggle item visibility"""
        if artifact_id not in self.hidden_items:
            self.hidden_items[artifact_id] = set()
        
        if show:
            self.hidden_items[artifact_id].discard(item_key)
        else:
            self.hidden_items[artifact_id].add(item_key)
        
        # Refresh table
        if artifact_id in self.artifact_data:
            self.update_table(artifact_id, self.artifact_data[artifact_id])
        # Update summary table
        self.update_summary_table()
    
    def show_item_visibility_settings(self):
        """Show item visibility settings dialog"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Item Visibility Settings")
        dialog.setMinimumSize(600, 400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # Instructions
        info_label = QLabel("Select items to hide/show. Hidden items will not appear in tables.")
        layout.addWidget(info_label)
        
        # List of hidden items by artifact
        list_widget = QListWidget()
        layout.addWidget(list_widget)
        
        # Populate list
        for artifact_id, hidden_set in self.hidden_items.items():
            if hidden_set:
                artifact_name = self.artifact_names.get(artifact_id, artifact_id)
                for item_key in hidden_set:
                    path, name = item_key.split('|', 1) if '|' in item_key else ('', item_key)
                    list_widget.addItem(f"[{artifact_name}] {name} - {path}")
                    list_widget.item(list_widget.count() - 1).setData(Qt.UserRole, (artifact_id, item_key))
        
        if list_widget.count() == 0:
            list_widget.addItem("No hidden items")
            list_widget.item(0).setFlags(Qt.NoItemFlags)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        btn_show_selected = QPushButton("Show Selected")
        btn_show_selected.clicked.connect(lambda: self.show_selected_items(list_widget))
        button_layout.addWidget(btn_show_selected)
        
        btn_show_all = QPushButton("Show All")
        btn_show_all.clicked.connect(lambda: self.show_all_hidden_items(list_widget))
        button_layout.addWidget(btn_show_all)
        
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)
        button_layout.addWidget(btn_close)
        
        layout.addLayout(button_layout)
        
        dialog.exec_()
    
    def show_selected_items(self, list_widget):
        """Show selected hidden items"""
        from PyQt5.QtWidgets import QMessageBox
        
        selected_items = list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "Information", "Please select items to show.")
            return
        
        for item in selected_items:
            data = item.data(Qt.UserRole)
            if data:
                artifact_id, item_key = data
                if artifact_id not in self.hidden_items:
                    self.hidden_items[artifact_id] = set()
                self.hidden_items[artifact_id].discard(item_key)
                list_widget.takeItem(list_widget.row(item))
        
        # Refresh tables
        for artifact_id in self.artifact_data:
            self.update_table(artifact_id, self.artifact_data[artifact_id])
        self.update_summary_table()
        
        if list_widget.count() == 0:
            list_widget.addItem("No hidden items")
            list_widget.item(0).setFlags(Qt.NoItemFlags)
    
    def show_all_hidden_items(self, list_widget):
        """Show all hidden items"""
        from PyQt5.QtWidgets import QMessageBox
        
        reply = QMessageBox.question(self, "Confirm", "Show all hidden items?",
                                    QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.hidden_items.clear()
            list_widget.clear()
            list_widget.addItem("No hidden items")
            list_widget.item(0).setFlags(Qt.NoItemFlags)
            
            # Refresh tables
            for artifact_id in self.artifact_data:
                self.update_table(artifact_id, self.artifact_data[artifact_id])
            self.update_summary_table()
    
    def on_table_cell_changed(self, table, row, col, artifact_id):
        """Handle checkbox state change in table"""
        if col != 0:  # Only handle checkbox column
            return
        
        checkbox_item = table.item(row, 0)
        if not checkbox_item:
            return
        
        item_key = checkbox_item.data(Qt.UserRole)
        if not item_key:
            return
        
        state = checkbox_item.checkState()
        self.on_item_visibility_changed(artifact_id, item_key, state)
    
    def show_artifact_detail(self, table, row, column):
        """아티팩트 결과 상세 보기 (원문/HEX)"""
        import sys
        try:
            print(f"[DEBUG] show_artifact_detail called: row={row}, column={column}", file=sys.stderr)
            sys.stderr.flush()
            
            if not table:
                print("[DEBUG] No table provided", file=sys.stderr)
                sys.stderr.flush()
                return
            
            # Skip if clicking on checkbox column
            if column == 0:
                print("[DEBUG] Clicked on checkbox column, skipping", file=sys.stderr)
                sys.stderr.flush()
                return
            
            # Check if row is valid
            if row < 0 or row >= table.rowCount():
                print(f"[DEBUG] Invalid row: {row}, table rowCount: {table.rowCount()}", file=sys.stderr)
                sys.stderr.flush()
                return

            def get_text(col):
                try:
                    item = table.item(row, col)
                    return item.text() if item else ""
                except:
                    return ""

            item_name = get_text(1)  # Adjusted for checkbox column
            file_path = get_text(2)  # Adjusted for checkbox column
            time_value = get_text(3)  # Adjusted for checkbox column
            original_time = get_text(4)  # Adjusted for checkbox column
            
            print(f"[DEBUG] Extracted: item_name={item_name}, file_path={file_path}", file=sys.stderr)
            sys.stderr.flush()
            
            # Skip if this is a status row (no file path)
            if not file_path or file_path.strip() == "":
                print("[DEBUG] No file path, skipping", file=sys.stderr)
                sys.stderr.flush()
                return

            match_hint = original_time or time_value
            header_text = (
                f"항목: {item_name}\n"
                f"파일 경로: {file_path}\n"
                f"시간: {time_value}\n"
                f"원본 시간: {original_time}"
            )
            abx_text = None
            if item_name and "appops" in item_name.lower() and self.reset_instance:
                abx_text = getattr(self.reset_instance, "last_abx_output", None)
            
            print(f"[DEBUG] Calling show_raw_hex_dialog with file_path: {file_path}", file=sys.stderr)
            sys.stderr.flush()
            self.show_raw_hex_dialog("아티팩트 상세", header_text, file_path, match_hint, abx_text=abx_text)
            print("[DEBUG] show_raw_hex_dialog completed", file=sys.stderr)
            sys.stderr.flush()
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = f"[ERROR] Exception in show_artifact_detail: {e}\n{error_trace}"
            print(error_msg, file=sys.stderr)
            sys.stderr.flush()
            try:
                if hasattr(self, 'log'):
                    self.log(f"[ERROR] Exception in show_artifact_detail: {e}")
                    self.log(error_trace)
            except:
                pass
            try:
                if hasattr(self, 'show_message'):
                    self.show_message("Error", f"Error showing artifact detail: {str(e)}")
            except:
                pass

    def show_raw_hex_dialog(self, title, header_text, file_path, match_hint, abx_text=None, context_item_name=None):
        """원문/HEX 뷰 다이얼로그 표시"""
        import sys
        try:
            print(f"[DEBUG] show_raw_hex_dialog called: title={title}, file_path={file_path}", file=sys.stderr)
            sys.stderr.flush()
            
            if not file_path:
                try:
                    if hasattr(self, 'show_message'):
                        self.show_message("상세 보기", "파일 경로가 없습니다.")
                except:
                    pass
                return

            item_name_context = (context_item_name or "").strip()
            if not item_name_context:
                try:
                    m_item = re.search(r"항목:\s*(.+)", header_text or "")
                    if m_item:
                        item_name_context = m_item.group(1).strip()
                except Exception:
                    item_name_context = ""
            is_summary_last_log = ("Summary Results" in str(title)) and ("last_log" in item_name_context.lower())

            try:
                text, raw_bytes, error = self.get_file_content_for_detail(file_path)
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                error_msg = f"[ERROR] Error getting file content: {e}\n{error_trace}"
                print(error_msg, file=sys.stderr)
                sys.stderr.flush()
                try:
                    if hasattr(self, 'log'):
                        self.log(f"Error getting file content: {e}")
                        self.log(error_trace)
                except:
                    pass
                try:
                    if hasattr(self, 'show_message'):
                        self.show_message("Error", f"Error getting file content: {str(e)}")
                except:
                    pass
                return

            print(f"[DEBUG] Creating QDialog", file=sys.stderr)
            sys.stderr.flush()
            dialog = QDialog(self)
            dialog.setWindowTitle(title)
            dialog_layout = QVBoxLayout()
            dialog.setLayout(dialog_layout)

            header = QTextEdit()
            header.setReadOnly(True)
            header.setPlainText(header_text)
            header.setFontFamily("Courier")
            header.setFixedHeight(110)
            dialog_layout.addWidget(header)

            tabs = QTabWidget()

            # 검색 바
            search_layout = QHBoxLayout()
            search_label = QLabel("찾기:")
            search_input = QLineEdit()
            search_input.setPlaceholderText("찾을 문자열 입력")
            btn_find_next = QPushButton("다음")
            btn_find_prev = QPushButton("이전")
            btn_jump_raw_hit = QPushButton("원문 hit")
            btn_jump_hex_hit = QPushButton("HEX hit")
            btn_last_log_recalc = None
            if is_summary_last_log:
                btn_last_log_recalc = QPushButton("get_system_time 재계산")
            search_layout.addWidget(search_label)
            search_layout.addWidget(search_input)
            search_layout.addWidget(btn_find_next)
            search_layout.addWidget(btn_find_prev)
            search_layout.addWidget(btn_jump_raw_hit)
            search_layout.addWidget(btn_jump_hex_hit)
            if btn_last_log_recalc:
                search_layout.addWidget(btn_last_log_recalc)
            dialog_layout.addLayout(search_layout)

            dialog_layout.addWidget(tabs)

            raw_text = QTextEdit()
            raw_text.setReadOnly(True)
            raw_text.setFontFamily("Courier")

            hex_text = QTextEdit()
            hex_text.setReadOnly(True)
            hex_text.setFontFamily("Courier")

            if error:
                raw_text.setPlainText(f"원문 데이터: {error}")
                hex_text.setPlainText(f"HEX 데이터: {error}")
                effective_match_hint = match_hint
            else:
                effective_match_hint = match_hint
                if text and file_path and ("ULR_PERSISTENT_PREFS.xml" in file_path or "URL_PERSISTENT_PREFS.xml" in file_path):
                    try:
                        m = re.search(r'reportingAutoenableManagerInitTimeMillisKey"\s+value="(\d+)"', text)
                        if m:
                            effective_match_hint = [match_hint, m.group(1)]
                    except Exception:
                        pass

                # 원문 탭에는 전체 텍스트 표시 (매칭 부분 강조)
                if text:
                    highlighted = self.format_text_highlight(text, effective_match_hint)
                    raw_text.setHtml(f"<pre>{highlighted}</pre>")
                elif raw_bytes:
                    # 텍스트로 변환할 수 없지만 바이너리 데이터가 있는 경우
                    # 추가 인코딩 시도 또는 바이너리 데이터를 텍스트로 표시 시도
                    text_attempt = None
                    for enc in ("latin-1", "cp1252", "iso-8859-1"):
                        try:
                            text_attempt = raw_bytes.decode(enc, errors='replace')
                            if text_attempt and len(text_attempt.strip()) > 0:
                                break
                        except:
                            continue
                    
                    if text_attempt and len(text_attempt.strip()) > 0:
                        # 일부 특수 문자를 제거하거나 표시 가능한 문자만 표시
                        display_text = ''.join(c if ord(c) < 128 or c.isprintable() else '.' for c in text_attempt)
                        # 하이라이팅 적용
                        highlighted = self.format_text_highlight(display_text, effective_match_hint)
                        raw_text.setHtml(f"<pre>텍스트 변환 (부분 성공):\n{highlighted}\n\n(원본 바이너리 데이터는 HEX 탭에서 확인하세요)</pre>")
                    else:
                        raw_text.setPlainText(f"텍스트로 변환할 수 없는 바이너리 데이터입니다.\n파일 크기: {len(raw_bytes)} bytes\n\nHEX 탭에서 바이너리 데이터를 확인하세요.")
                else:
                    raw_text.setPlainText("텍스트 데이터가 없습니다.")

                byte_offset, encoding, match_len = self.find_byte_offset(raw_bytes, effective_match_hint) if raw_bytes else (None, None, None)
                if raw_bytes:
                    hex_view = self.format_hex_view(raw_bytes, byte_offset, encoding, show_full=True)
                    hex_text.setPlainText(hex_view)
                else:
                    hex_text.setPlainText("HEX 데이터가 없습니다.")

            tabs.addTab(raw_text, "원문")
            tabs.addTab(hex_text, "HEX/디코딩")

            # internal.db는 바이너리 DB 구조라 문자열 hit가 어려워 DB hit 탭 제공
            if not error and raw_bytes and file_path and "internal.db" in file_path.lower():
                db_hit_info = self.extract_internal_db_hit_info(raw_bytes, effective_match_hint)
                db_hit_tab = QTextEdit()
                db_hit_tab.setReadOnly(True)
                db_hit_tab.setFontFamily("Courier")
                if db_hit_info:
                    db_hit_tab.setPlainText(db_hit_info)
                else:
                    db_hit_tab.setPlainText(
                        "DB hit 결과가 없습니다.\n"
                        "- files 테이블이 없거나\n"
                        "- date_added/date_modified에서 hit 후보와 일치하는 값이 없을 수 있습니다."
                    )
                tabs.addTab(db_hit_tab, "DB hit")

            # 매칭 위치 강조 탭
            if not error and byte_offset is not None and match_len:
                highlight = QTextEdit()
                highlight.setReadOnly(True)
                highlight.setFontFamily("Courier")
                highlight_html = self.format_hex_view_highlight(
                    raw_bytes,
                    byte_offset,
                    match_len,
                    encoding
                )
                highlight.setHtml(highlight_html)
                tabs.addTab(highlight, "매칭 위치")

            if abx_text:
                abx_tab = QTextEdit()
                abx_tab.setReadOnly(True)
                abx_tab.setFontFamily("Courier")
                abx_tab.setPlainText(abx_text)
                tabs.addTab(abx_tab, "ABX 결과")

            def get_active_text_edit():
                current = tabs.currentWidget()
                if isinstance(current, QTextEdit):
                    return current
                return None

            def do_find(forward=True):
                needle = search_input.text()
                if not needle:
                    return
                edit = get_active_text_edit()
                if not edit:
                    return
                flags = QTextDocument.FindFlags()
                if not forward:
                    flags |= QTextDocument.FindBackward
                if not edit.find(needle, flags):
                    # 처음/끝으로 되돌려 다시 탐색
                    cursor = edit.textCursor()
                    cursor.movePosition(QTextCursor.Start if forward else QTextCursor.End)
                    edit.setTextCursor(cursor)
                    edit.find(needle, flags)

            def jump_to_raw_hit():
                tabs.setCurrentWidget(raw_text)
                candidates = self._build_match_candidates(effective_match_hint)
                if not candidates:
                    self.show_message("안내", "hit 후보를 찾을 수 없습니다.")
                    return
                cursor = raw_text.textCursor()
                cursor.movePosition(QTextCursor.Start)
                raw_text.setTextCursor(cursor)
                for candidate in candidates:
                    if raw_text.find(candidate):
                        raw_text.ensureCursorVisible()
                        return
                self.show_message("안내", "원문에서 hit 위치를 찾지 못했습니다.")

            def jump_to_hex_hit():
                tabs.setCurrentWidget(hex_text)
                if byte_offset is None:
                    self.show_message("안내", "HEX에서 이동할 hit 위치가 없습니다.")
                    return
                line_addr = (byte_offset // 16) * 16
                addr_text = f"{line_addr:08X}"
                cursor = hex_text.textCursor()
                cursor.movePosition(QTextCursor.Start)
                hex_text.setTextCursor(cursor)
                if hex_text.find(addr_text):
                    hex_text.ensureCursorVisible()
                else:
                    self.show_message("안내", "HEX에서 hit 위치를 찾지 못했습니다.")

            def run_last_log_recalc():
                if not is_summary_last_log:
                    return
                text_data = text
                if (not text_data) and raw_bytes:
                    try:
                        text_data = raw_bytes.decode("utf-8", errors="ignore")
                    except Exception:
                        text_data = ""
                result_msg = self.apply_last_log_recalc_from_text(file_path, text_data)
                self.show_message("last_log 재계산", result_msg)

            btn_find_next.clicked.connect(lambda: do_find(True))
            btn_find_prev.clicked.connect(lambda: do_find(False))
            search_input.returnPressed.connect(lambda: do_find(True))
            btn_jump_raw_hit.clicked.connect(jump_to_raw_hit)
            btn_jump_hex_hit.clicked.connect(jump_to_hex_hit)
            if btn_last_log_recalc:
                btn_last_log_recalc.clicked.connect(run_last_log_recalc)

            print(f"[DEBUG] Showing dialog", file=sys.stderr)
            sys.stderr.flush()
            dialog.resize(800, 600)
            dialog.exec_()
            print(f"[DEBUG] Dialog closed", file=sys.stderr)
            sys.stderr.flush()
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = f"[ERROR] Exception in show_raw_hex_dialog: {e}\n{error_trace}"
            print(error_msg, file=sys.stderr)
            sys.stderr.flush()
            try:
                if hasattr(self, 'log'):
                    self.log(f"[ERROR] Exception in show_raw_hex_dialog: {e}")
                    self.log(error_trace)
            except:
                pass
            try:
                if hasattr(self, 'show_message'):
                    self.show_message("Error", f"Error showing raw hex dialog: {str(e)}")
            except:
                pass

    def parse_last_log_get_system_time_timeline(self, content_text):
        """Parse last_log get_system_time base and rebuild timeline."""
        if not content_text:
            return None
        lines = content_text.splitlines()
        base_rel = None
        base_dt = None
        rel_pattern = re.compile(r'^\[\s*(\d+\.\d+)\]\s*(.*)$')
        gst_pattern = re.compile(r'get_system_time=(\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2})')
        reset_keywords = (
            "factory_reset",
            "wipe_data",
            "wipe data",
            "data wipe",
            "master clear",
            "format /data",
            "userdata"
        )

        for line in lines:
            m_rel = rel_pattern.match(line)
            if not m_rel:
                continue
            rel = float(m_rel.group(1))
            msg = m_rel.group(2)
            m_gst = gst_pattern.search(msg)
            if not m_gst:
                continue
            try:
                dt = datetime.strptime(m_gst.group(1), "%Y-%m-%d-%H:%M:%S")
            except Exception:
                continue
            base_rel = rel
            base_dt = dt
            break

        if base_rel is None or base_dt is None:
            return None

        timeline = []
        for line in lines:
            m_rel = rel_pattern.match(line)
            if not m_rel:
                continue
            rel = float(m_rel.group(1))
            msg = m_rel.group(2).strip()
            if not any(keyword in msg.lower() for keyword in reset_keywords):
                continue
            delta_sec = rel - base_rel
            abs_dt = base_dt + timedelta(seconds=delta_sec)
            timeline.append({
                "abs_dt": abs_dt,
                "rel": rel,
                "msg": msg
            })

        return {
            "base_dt": base_dt,
            "base_rel": base_rel,
            "timeline": timeline
        }

    def apply_last_log_recalc_from_text(self, file_path, content_text):
        """Apply get_system_time recalculation from summary detail dialog."""
        parsed = self.parse_last_log_get_system_time_timeline(content_text)
        if not parsed:
            return "get_system_time 기준점을 찾지 못했습니다."

        artifact_id = "22"
        if artifact_id not in self.artifact_data:
            self.artifact_data[artifact_id] = []

        # 기존 동일 파일 재계산 결과 제거 후 다시 생성
        before_count = len(self.artifact_data[artifact_id])
        self.artifact_data[artifact_id] = [
            item for item in self.artifact_data[artifact_id]
            if not (
                str(item.get("path", "")) == str(file_path)
                and "last_log (재계산" in str(item.get("name", ""))
            )
        ]
        removed_count = before_count - len(self.artifact_data[artifact_id])
        self.update_table(artifact_id, self.artifact_data[artifact_id])
        self.update_summary_table()

        base_dt = parsed["base_dt"]
        self.add_artifact_data(
            artifact_id,
            "last_log (재계산 base)",
            file_path,
            base_dt,
            None,
            is_kst=True,
            original_time=f"get_system_time={base_dt.strftime('%Y-%m-%d-%H:%M:%S')}"
        )

        for event in parsed["timeline"]:
            self.add_artifact_data(
                artifact_id,
                "last_log (재계산 event)",
                file_path,
                event["abs_dt"],
                event["msg"],
                is_kst=True,
                original_time=f"[{event['rel']:.6f}] {event['msg']}"
            )

        return (
            f"재계산 완료: base 1건 + event {len(parsed['timeline'])}건"
            f" (이전 재계산 {removed_count}건 교체)"
        )

    def get_file_content_for_detail(self, file_path):
        """파일 원문/바이트 데이터 가져오기"""
        # reset_instance가 있으면 사용
        if self.reset_instance:
            try:
                if getattr(self.reset_instance, "choice", None) == "2":
                    text = self.reset_instance.adb_read_file_for_search(file_path)
                    raw_bytes = self.reset_instance.adb_read_file_bytes(file_path)
                else:
                    text = self.reset_instance.read_file_for_search(file_path)
                    raw_bytes = self.reset_instance.read_file_bytes(file_path)
                
                if text or raw_bytes:
                    return text or "", raw_bytes or b"", None
            except Exception as e:
                pass  # 실패하면 저장된 경로로 시도
        
        # reset_instance가 없거나 실패한 경우, 저장된 파일 경로로 직접 읽기 시도
        if not self.saved_file_path or not self.saved_source:
            return None, None, f"저장된 파일 경로 정보가 없습니다. (file_path={self.saved_file_path}, source={self.saved_source})"
        
        try:
            import zipfile
            import os
            
            # saved_source가 숫자 문자열인 경우 변환
            source_map = {"1": "ZIP", "2": "ADB", "3": "Folder"}
            if self.saved_source in source_map:
                self.saved_source = source_map[self.saved_source]
            
            if self.saved_source == "ZIP":
                # ZIP 파일에서 읽기
                if not os.path.exists(self.saved_file_path):
                    return None, None, f"ZIP 파일이 존재하지 않습니다: {self.saved_file_path}"
                
                if not zipfile.is_zipfile(self.saved_file_path):
                    return None, None, f"ZIP 파일이 아닙니다: {self.saved_file_path}"
                
                # 경로 정규화 (ZIP 내부는 '/' 사용)
                file_path_norm = str(file_path or "").replace("\\", "/")

                # 여러 경로 후보 시도
                path_candidates = [
                    file_path_norm,  # 원본 경로
                    file_path_norm.lstrip("/"),  # 앞의 / 제거
                    file_path_norm.replace("Dump/", ""),  # Dump/ 제거
                    file_path_norm.replace("Dump/", "").lstrip("/"),  # Dump/ 제거 후 / 제거
                ]
                
                if file_path_norm.startswith("Dump/"):
                    path_candidates.append(file_path_norm[5:])  # Dump/ 제거
                else:
                    path_candidates.append(f"Dump/{file_path_norm}")  # Dump/ 추가
                    path_candidates.append(f"Dump/{file_path_norm.lstrip('/')}")  # Dump/ 추가 후 / 제거
                
                with zipfile.ZipFile(self.saved_file_path, 'r') as zf:
                    zip_file_list = zf.namelist()
                    zip_file_lower_map = {p.lower(): p for p in zip_file_list}
                    
                    for zip_path in path_candidates:
                        # 정확한 매칭 시도
                        actual_zip_path = None
                        if zip_path in zip_file_list:
                            actual_zip_path = zip_path
                        else:
                            actual_zip_path = zip_file_lower_map.get(zip_path.lower())

                        if actual_zip_path:
                            try:
                                with zf.open(actual_zip_path) as f:
                                    raw_bytes = f.read()
                                # 텍스트로 변환 시도
                                text = None
                                for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                                    try:
                                        text = raw_bytes.decode(enc)
                                        break
                                    except:
                                        continue
                                return text or "", raw_bytes, None
                            except Exception as e:
                                continue
                        
                        # 부분 매칭 시도 (파일명만으로)
                        file_name = os.path.basename(zip_path)
                        file_name_lower = file_name.lower()
                        for zf_path in zip_file_list:
                            zf_lower = zf_path.lower()
                            if zf_lower.endswith(file_name_lower) or zf_lower.endswith(f"/{file_name_lower}"):
                                try:
                                    with zf.open(zf_path) as f:
                                        raw_bytes = f.read()
                                    # 텍스트로 변환 시도
                                    text = None
                                    for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                                        try:
                                            text = raw_bytes.decode(enc)
                                            break
                                        except:
                                            continue
                                    return text or "", raw_bytes, None
                                except Exception as e:
                                    continue

                    # internal.db는 경로가 자주 달라져서 마지막 fallback 수행
                    if os.path.basename(file_path_norm).lower() == "internal.db":
                        for zf_path in zip_file_list:
                            if zf_path.lower().endswith("/internal.db") or zf_path.lower() == "internal.db":
                                try:
                                    with zf.open(zf_path) as f:
                                        raw_bytes = f.read()
                                    text = None
                                    for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                                        try:
                                            text = raw_bytes.decode(enc)
                                            break
                                        except:
                                            continue
                                    return text or "", raw_bytes, None
                                except Exception:
                                    continue
                
                return None, None, f"ZIP 파일에서 파일을 찾을 수 없습니다: {file_path} (시도한 경로: {path_candidates})"
            
            elif self.saved_source == "Folder":
                # 폴더에서 직접 읽기
                file_path_norm = str(file_path or "").replace("\\", "/")
                # 여러 경로 후보 시도
                path_candidates = [
                    os.path.join(self.saved_file_path, file_path_norm),
                    os.path.join(self.saved_file_path, file_path_norm.lstrip("/")),
                    os.path.join(self.saved_file_path, file_path_norm.replace("Dump/", "")),
                    os.path.join(self.saved_file_path, file_path_norm.replace("Dump/", "").lstrip("/")),
                ]
                
                if file_path_norm.startswith("Dump/"):
                    path_candidates.append(os.path.join(self.saved_file_path, file_path_norm[5:]))
                else:
                    path_candidates.append(os.path.join(self.saved_file_path, "Dump", file_path_norm))
                    path_candidates.append(os.path.join(self.saved_file_path, "Dump", file_path_norm.lstrip("/")))
                
                # 파일명만으로도 시도
                file_name = os.path.basename(file_path_norm)
                if file_name:
                    file_name_lower = file_name.lower()
                    for root, dirs, files in os.walk(self.saved_file_path):
                        for f in files:
                            if f.lower() == file_name_lower:
                                path_candidates.append(os.path.join(root, f))
                
                for full_path in path_candidates:
                    if os.path.exists(full_path) and os.path.isfile(full_path):
                        try:
                            with open(full_path, 'rb') as f:
                                raw_bytes = f.read()
                            # 텍스트로 변환 시도
                            text = None
                            for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                                try:
                                    text = raw_bytes.decode(enc)
                                    break
                                except:
                                    continue
                            return text or "", raw_bytes, None
                        except Exception as e:
                            continue
                
                return None, None, f"폴더에서 파일을 찾을 수 없습니다: {file_path} (시도한 경로: {path_candidates[:5]})"
            else:
                return None, None, f"지원하지 않는 소스 타입: {self.saved_source}"
        except Exception as e:
            import traceback
            return None, None, f"저장된 파일 경로에서 읽기 실패: {e}\n{traceback.format_exc()}"

    def build_text_snippet(self, text, match_hint):
        """매칭 힌트를 기준으로 주변 텍스트 추출"""
        if not text:
            return "", None

        for raw_hint in self._build_match_candidates(match_hint):
            idx = text.find(raw_hint)
            if idx != -1:
                before = text[:idx]
                line_no = before.count("\n") + 1
                lines = text.splitlines()
                line_idx = max(0, line_no - 1)
                start = max(0, line_idx - 3)
                end = min(len(lines), line_idx + 4)
                snippet = "\n".join(lines[start:end])
                return snippet, line_no

        # 힌트를 못 찾으면 전체 표시
        return text, None

    def _build_match_candidates(self, match_hint):
        """Build multiple match candidates (raw/epoch sec/ms/timezone shifted)."""
        if not match_hint:
            return []

        if isinstance(match_hint, (list, tuple, set)):
            raw_values = list(match_hint)
        else:
            raw_values = [match_hint]

        candidates = []
        seen = set()

        def add_candidate(v):
            if not v:
                return
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                candidates.append(s)

        for raw in raw_values:
            base = str(raw).replace(" (시간 없음)", "").strip()
            if not base:
                continue
            add_candidate(base)

            # last_log 재계산 힌트 보정:
            # - "라인: ..." / "라인 N: ..." prefix 제거
            # - "[  0.123456] ..."에서 메시지 본문 추출
            line_msg = re.match(r"^라인(?:\s+\d+)?\s*:\s*(.+)$", base)
            if line_msg:
                add_candidate(line_msg.group(1).strip())

            rel_msg = re.match(r"^\[\s*\d+\.\d+\]\s*(.+)$", base)
            if rel_msg:
                add_candidate(rel_msg.group(1).strip())

            # get_system_time=YYYY-MM-DD HH:MM:SS <-> YYYY-MM-DD-HH:MM:SS 상호 변환
            gst_match = re.search(
                r"(get_system_time=)(\d{4}-\d{2}-\d{2})[ -](\d{2}:\d{2}:\d{2})",
                base
            )
            if gst_match:
                prefix, day_part, time_part = gst_match.groups()
                add_candidate(f"{prefix}{day_part}-{time_part}")
                add_candidate(f"{prefix}{day_part} {time_part}")

            numeric = None
            if re.fullmatch(r"\d{10,16}", base):
                numeric = base
            elif re.fullmatch(r"\d+\.\d+", base):
                try:
                    f = float(base)
                    if f.is_integer():
                        numeric = str(int(f))
                except Exception:
                    pass

            if numeric:
                try:
                    n = int(numeric)
                    if len(numeric) >= 13:
                        add_candidate(str(n // 1000))
                    else:
                        add_candidate(str(n * 1000))
                except Exception:
                    pass
            else:
                # Datetime-like hint: generate epoch sec/ms candidates.
                dt = self.parse_time_text(base)
                if dt:
                    try:
                        ts = int(dt.timestamp())
                        add_candidate(str(ts))
                        add_candidate(str(ts * 1000))
                        # Some artifacts may be stored with UTC/KST shift.
                        ts_plus_9h = int((dt + timedelta(hours=9)).timestamp())
                        ts_minus_9h = int((dt - timedelta(hours=9)).timestamp())
                        add_candidate(str(ts_plus_9h))
                        add_candidate(str(ts_plus_9h * 1000))
                        add_candidate(str(ts_minus_9h))
                        add_candidate(str(ts_minus_9h * 1000))
                    except Exception:
                        pass

        return candidates

    def extract_internal_db_hit_info(self, raw_bytes, match_hint):
        """internal.db에서 hit 후보(date_added/date_modified) 행 정보를 추출"""
        if not raw_bytes:
            return "DB hit 진단: raw_bytes가 비어 있습니다."

        try:
            import sqlite3
        except Exception:
            return self.extract_internal_db_binary_hit_info(raw_bytes, match_hint)

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tf:
                tf.write(raw_bytes)
                temp_path = tf.name

            conn = sqlite3.connect(temp_path)
            cur = conn.cursor()

            candidate_ints = set()
            for c in self._build_match_candidates(match_hint):
                s = str(c).strip()
                try:
                    n = int(float(s))
                    candidate_ints.add(n)
                    if abs(n) >= 10**12:
                        candidate_ints.add(n // 1000)
                    else:
                        candidate_ints.add(n * 1000)
                except Exception:
                    continue

            if not candidate_ints:
                conn.close()
                return "DB hit 진단: match_hint에서 숫자 후보를 생성하지 못했습니다."

            values = list(sorted(candidate_ints))[:100]
            placeholders = ",".join(["?"] * len(values))

            # files 고정이 아니라 date_added 컬럼을 가진 테이블을 탐색
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            table_names = [r[0] for r in cur.fetchall()]
            candidate_tables = []
            for t in table_names:
                try:
                    cur.execute(f"PRAGMA table_info('{t}')")
                    cols = [r[1] for r in cur.fetchall()]
                    if "date_added" in cols:
                        candidate_tables.append((t, cols))
                except Exception:
                    continue

            if not candidate_tables:
                conn.close()
                return (
                    "DB hit 결과가 없습니다.\n"
                    f"- 숫자 후보: {values[:10]}\n"
                    "- date_added 컬럼을 가진 테이블이 없습니다."
                )

            result_blocks = []
            for table_name, existing_cols in candidate_tables:
                preferred_cols = ["_id", "date_added", "date_modified", "datetaken", "_data", "title", "mime_type"]
                display_cols = [c for c in preferred_cols if c in existing_cols]
                if not display_cols:
                    display_cols = existing_cols[:6]
                if not display_cols:
                    continue

                where_parts = [f"CAST(date_added AS INTEGER) IN ({placeholders})"]
                params = list(values)
                if "date_modified" in existing_cols:
                    where_parts.append(f"CAST(date_modified AS INTEGER) IN ({placeholders})")
                    params.extend(values)

                query = f"""
                    SELECT {", ".join(display_cols)}
                    FROM "{table_name}"
                    WHERE {" OR ".join(where_parts)}
                    LIMIT 30
                """
                try:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                except Exception:
                    rows = []

                if rows:
                    block = [f"[table: {table_name}]", " | ".join(display_cols)]
                    for row in rows:
                        block.append(" | ".join(str(v) for v in row))
                    result_blocks.append("\n".join(block))

            if result_blocks:
                conn.close()
                return "internal.db hit rows\n" + ("-" * 60) + "\n" + ("\n\n".join(result_blocks))

            # exact match 실패 시 근접값 참고 정보 제공
            base = values[0]
            nearest_lines = []
            for table_name, existing_cols in candidate_tables:
                if "date_added" not in existing_cols:
                    continue
                try:
                    q = f"""
                        SELECT date_added
                        FROM "{table_name}"
                        WHERE date_added IS NOT NULL
                        ORDER BY ABS(CAST(date_added AS INTEGER) - ?)
                        LIMIT 3
                    """
                    cur.execute(q, (base,))
                    near_rows = [r[0] for r in cur.fetchall()]
                    if near_rows:
                        nearest_lines.append(f"{table_name}: {near_rows}")
                except Exception:
                    continue
            if nearest_lines:
                conn.close()
                return (
                    "DB hit 결과가 없습니다.\n"
                    f"후보값(일부): {values[:10]}\n"
                    f"탐색 테이블: {[t for t, _ in candidate_tables]}\n"
                    "근접 date_added 값 참고:\n" + "\n".join(nearest_lines)
                )
            conn.close()
            return (
                "DB hit 결과가 없습니다.\n"
                f"후보값(일부): {values[:10]}\n"
                f"탐색 테이블: {[t for t, _ in candidate_tables]}\n"
                "- 근접값도 조회되지 않았습니다."
            )
        except Exception as e:
            return f"DB hit 진단 오류: {e}"
        finally:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

    def extract_internal_db_binary_hit_info(self, raw_bytes, match_hint):
        """sqlite3 모듈 없이 internal.db 바이너리에서 hit 후보를 탐색"""
        try:
            candidate_ints = set()
            for c in self._build_match_candidates(match_hint):
                s = str(c).strip()
                try:
                    n = int(float(s))
                    candidate_ints.add(n)
                    if abs(n) >= 10**12:
                        candidate_ints.add(n // 1000)
                    else:
                        candidate_ints.add(n * 1000)
                except Exception:
                    continue

            if not candidate_ints:
                return (
                    "DB hit 결과가 없습니다.\n"
                    "sqlite3 모듈이 없어 바이너리 패턴 탐색으로 대체했지만,\n"
                    "숫자 후보를 생성하지 못했습니다."
                )

            values = list(sorted(candidate_ints))[:60]
            patterns = []
            for v in values:
                if v < 0:
                    continue
                s = str(v).encode("ascii", errors="ignore")
                if s:
                    patterns.append((v, "ascii", s))
                try:
                    if v <= 0xFFFFFFFF:
                        patterns.append((v, "u32_le", struct.pack("<I", v)))
                        patterns.append((v, "u32_be", struct.pack(">I", v)))
                    if v <= 0xFFFFFFFFFFFFFFFF:
                        patterns.append((v, "u64_le", struct.pack("<Q", v)))
                        patterns.append((v, "u64_be", struct.pack(">Q", v)))
                except Exception:
                    continue

            # SQLite 헤더에서 페이지 크기 추정 (없으면 기본 4096)
            page_size = 4096
            try:
                if len(raw_bytes) >= 18 and raw_bytes[:16] == b"SQLite format 3\x00":
                    ps = int.from_bytes(raw_bytes[16:18], byteorder="big")
                    page_size = 65536 if ps == 1 else (ps if ps > 0 else 4096)
            except Exception:
                page_size = 4096

            def _render_context(offset, pat_len):
                ctx_before = 16
                ctx_after = 16
                s = max(0, offset - ctx_before)
                e = min(len(raw_bytes), offset + pat_len + ctx_after)
                chunk = raw_bytes[s:e]
                hex_txt = " ".join(f"{b:02X}" for b in chunk)
                ascii_txt = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
                return hex_txt, ascii_txt, s, e

            hit_lines = []
            for val, enc, pat in patterns:
                if not pat:
                    continue
                offsets = []
                start = 0
                while True:
                    idx = raw_bytes.find(pat, start)
                    if idx == -1:
                        break
                    offsets.append(idx)
                    if len(offsets) >= 5:
                        break
                    start = idx + 1
                if offsets:
                    hit_lines.append(f"value={val} [{enc}] hits={len(offsets)}")
                    for o in offsets[:3]:
                        page_no = (o // page_size) + 1 if page_size > 0 else 1
                        page_off = o % page_size if page_size > 0 else o
                        hex_txt, ascii_txt, ctx_s, ctx_e = _render_context(o, len(pat))
                        hit_lines.append(
                            f"  - offset=0x{o:08X} ({o}), page={page_no}, page_offset=0x{page_off:04X}, context={ctx_s}:{ctx_e}"
                        )
                        hit_lines.append(f"    hex: {hex_txt}")
                        hit_lines.append(f"    asc: {ascii_txt}")

            if hit_lines:
                return (
                    "DB hit (binary fallback; sqlite3 unavailable)\n"
                    + "-" * 60
                    + "\n"
                    + "\n".join(hit_lines[:60])
                )

            return (
                "DB hit 결과가 없습니다.\n"
                "sqlite3 모듈이 없어 바이너리 패턴 탐색으로 대체했습니다.\n"
                f"후보값(일부): {values[:10]}\n"
                "- ASCII/LE/BE 패턴 매칭 없음"
            )
        except Exception as e:
            return f"DB hit 바이너리 탐색 오류: {e}"

    def find_byte_offset(self, raw_bytes, match_hint):
        """바이트 오프셋과 인코딩/길이 추정"""
        if not raw_bytes or not match_hint:
            return None, None, None

        for raw_hint in self._build_match_candidates(match_hint):
            for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                try:
                    encoded = raw_hint.encode(enc)
                    pos = raw_bytes.find(encoded)
                    if pos != -1:
                        return pos, enc, len(encoded)
                except Exception:
                    continue

        return None, None, None

    def get_deep_search_raw_data(self, file_path, match_value):
        """Find original data for deep search results from file"""
        # reset_instance가 있으면 사용
        if self.reset_instance:
            try:
                if getattr(self.reset_instance, "choice", None) == "2":
                    content = self.reset_instance.adb_read_file_for_search(file_path)
                    raw_bytes = self.reset_instance.adb_read_file_bytes(file_path)
                else:
                    content = self.reset_instance.read_file_for_search(file_path)
                    raw_bytes = self.reset_instance.read_file_bytes(file_path)
                
                if content or raw_bytes:
                    return (content or "", raw_bytes or b""), None
            except Exception as e:
                pass  # 실패하면 저장된 경로로 시도
        
        # reset_instance가 없거나 실패한 경우, 저장된 파일 경로로 직접 읽기 시도
        if self.saved_file_path and self.saved_source:
            try:
                import zipfile
                import os
                
                # saved_source가 숫자 문자열인 경우 변환
                source_map = {"1": "ZIP", "2": "ADB", "3": "Folder"}
                if self.saved_source in source_map:
                    self.saved_source = source_map[self.saved_source]
                
                if self.saved_source == "ZIP":
                    # ZIP 파일에서 읽기
                    if os.path.exists(self.saved_file_path) and zipfile.is_zipfile(self.saved_file_path):
                        with zipfile.ZipFile(self.saved_file_path, 'r') as zf:
                            # Dump/ 접두사 제거 시도
                            zip_path = file_path
                            if zip_path.startswith("Dump/"):
                                zip_path = zip_path[5:]
                            elif not zip_path.startswith("Dump/") and not zip_path.startswith("/"):
                                zip_path = f"Dump/{zip_path}"
                            
                            try:
                                with zf.open(zip_path) as f:
                                    raw_bytes = f.read()
                                    # 텍스트로 변환 시도
                                    content = None
                                    for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                                        try:
                                            content = raw_bytes.decode(enc)
                                            break
                                        except:
                                            continue
                                    return (content or "", raw_bytes), None
                            except:
                                pass
                
                elif self.saved_source == "Folder":
                    # 폴더에서 직접 읽기
                    full_path = os.path.join(self.saved_file_path, file_path)
                    if os.path.exists(full_path):
                        try:
                            with open(full_path, 'rb') as f:
                                raw_bytes = f.read()
                            # 텍스트로 변환 시도
                            content = None
                            for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                                try:
                                    content = raw_bytes.decode(enc)
                                    break
                                except:
                                    continue
                            return (content or "", raw_bytes), None
                        except Exception as e:
                            return None, f"저장된 파일 경로에서 읽기 실패: {e}"
            except Exception as e:
                return None, f"저장된 파일 경로에서 읽기 실패: {e}"
        
        return None, "분석 인스턴스가 없고 저장된 파일 경로도 사용할 수 없습니다."

    def build_binary_patterns(self, time_dt):
        """시간 값을 바이너리 패턴으로 변환"""
        patterns = {}
        epoch_sec = int(time_dt.timestamp())
        epoch_ms = int(time_dt.timestamp() * 1000)

        # 32-bit/64-bit seconds
        patterns["epoch_sec_le32"] = struct.pack("<I", epoch_sec & 0xFFFFFFFF)
        patterns["epoch_sec_be32"] = struct.pack(">I", epoch_sec & 0xFFFFFFFF)
        patterns["epoch_sec_le64"] = struct.pack("<Q", epoch_sec)
        patterns["epoch_sec_be64"] = struct.pack(">Q", epoch_sec)

        # 64-bit milliseconds
        patterns["epoch_ms_le64"] = struct.pack("<Q", epoch_ms)
        patterns["epoch_ms_be64"] = struct.pack(">Q", epoch_ms)

        return patterns

    def get_file_mod_time_for_search(self, file_path):
        """Get file modification time for deep search"""
        try:
            if getattr(self, "choice", None) == "2":
                return self.adb_get_mod_time(file_path)
            return self.get_mod_time_from_zip(file_path)
        except Exception:
            return None

    def format_hex_view(self, raw_bytes, byte_offset=None, encoding=None, context_size=128, show_full=False):
        """HEX + ASCII 형태로 원문 데이터를 보기 좋게 포맷"""
        if not raw_bytes:
            return "HEX 데이터를 생성할 수 없습니다."

        if show_full:
            start = 0
        elif byte_offset is None:
            start = 0
        else:
            start = max(0, byte_offset - context_size)
        end = len(raw_bytes) if show_full else min(len(raw_bytes), start + (context_size * 2))
        chunk = raw_bytes[start:end]

        lines = []
        header = f"바이트 범위: {start} ~ {end} (인코딩 추정: {encoding or '알 수 없음'})"
        lines.append(header)
        lines.append("")

        for i in range(0, len(chunk), 16):
            line_bytes = chunk[i:i+16]
            hex_part = " ".join(f"{b:02X}" for b in line_bytes)
            ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in line_bytes)
            lines.append(f"{start + i:08X}  {hex_part:<47}  {ascii_part}")

        return "\n".join(lines)

    def format_hex_view_highlight(self, raw_bytes, offset, length, encoding, context_size=64):
        """HEX + ASCII 형태로 매칭 위치 강조 (HTML)"""
        if not raw_bytes or offset is None or not length:
            return "<pre>매칭 위치를 표시할 수 없습니다.</pre>"

        start = max(0, offset - context_size)
        end = min(len(raw_bytes), offset + length + context_size)
        chunk = raw_bytes[start:end]

        lines = []
        header = f"바이트 범위: {start} ~ {end} (인코딩 추정: {encoding or '알 수 없음'})"
        lines.append(header)
        lines.append("")

        highlight_start = offset - start
        highlight_end = highlight_start + length

        for i in range(0, len(chunk), 16):
            line_bytes = chunk[i:i+16]
            hex_parts = []
            ascii_parts = []
            for j, b in enumerate(line_bytes):
                idx = i + j
                in_hl = highlight_start <= idx < highlight_end
                hx = f"{b:02X}"
                ch = chr(b) if 32 <= b <= 126 else "."
                if in_hl:
                    hex_parts.append(f"<span style='background-color:#ffe66b;font-weight:bold'>{hx}</span>")
                    ascii_parts.append(f"<span style='background-color:#ffe66b;font-weight:bold'>{ch}</span>")
                else:
                    hex_parts.append(hx)
                    ascii_parts.append(ch)
            hex_part = " ".join(hex_parts)
            ascii_part = "".join(ascii_parts)
            lines.append(f"{start + i:08X}  {hex_part:<47}  {ascii_part}")

        html = "<pre>" + "\n".join(lines) + "</pre>"
        return html

    def format_text_highlight(self, text, match_hint):
        """원문 텍스트에서 매칭 문자열 강조 (HTML)"""
        escaped = html.escape(text or "")
        if not match_hint:
            return escaped

        # 후보(원본/초/밀리초/시간대 보정)를 순회하며 모두 강조
        for candidate in self._build_match_candidates(match_hint):
            needle = html.escape(candidate)
            if not needle:
                continue

            # persistent_properties 패턴 강조
            if re.match(r'^\d{10,}$', needle):
                persistent_pattern = rf"reboot,factory_reset,{re.escape(needle)}"
                try:
                    pattern = re.compile(persistent_pattern, re.IGNORECASE)
                    escaped = pattern.sub(r"<span style='background-color:#ffe66b;font-weight:bold'>\g<0></span>", escaped)
                except Exception:
                    pass

            try:
                pattern = re.compile(re.escape(needle), re.IGNORECASE)
                escaped = pattern.sub(r"<span style='background-color:#ffe66b;font-weight:bold'>\g<0></span>", escaped)
            except Exception:
                pass
        
        return escaped
    
    def update_deep_search_progress(self, current, total):
        """Update deep search progress"""
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            # 진행률 텍스트 표시
            percentage = int((current / total) * 100) if total > 0 else 0
            remaining = total - current
            self.progress_bar.setFormat(f"Deep Search in progress... {current}/{total} ({percentage}%) - Remaining files: {remaining}")
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("Deep Search in progress...")
    
    def deep_search_finished(self):
        """Handle deep search completion"""
        self.progress_bar.setVisible(False)
        self.progress_bar.setFormat("")  # 진행률 텍스트 초기화
        self.btn_deep_search.setEnabled(True)
        
        # Move to deep search results tab
        for i in range(self.result_tabs.count()):
            if self.result_tabs.tabText(i) == "Deep Search Results":
                self.result_tabs.setCurrentIndex(i)
                break
        
        # Deep search results are saved together with analysis results, so no separate save needed
        # (사용자가 수동으로 저장할 수 있음)
    
    def _convert_to_json_serializable(self, obj):
        """datetime 객체를 JSON 직렬화 가능한 형태로 변환"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._convert_to_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_to_json_serializable(item) for item in obj]
        elif isinstance(obj, (int, float, str, bool, type(None))):
            return obj
        else:
            # 기타 타입은 문자열로 변환
            return str(obj)
    
    def save_analysis_result(self):
        """분석 결과를 JSON 파일로 저장"""
        try:
            # 저장할 데이터 구성 (파일명은 나중에 추가)
            save_data = {
                'timestamp': datetime.now().isoformat(),
                'file_path': self.file_path_edit.text() if hasattr(self, 'file_path_edit') else '',
                'source': self.get_selected_source() if hasattr(self, 'get_selected_source') else '',
                'artifact_data': {},
                'deep_search_results': [],
                'confirmed_time': self.confirmed_time_value if hasattr(self, 'confirmed_time_value') else None,
                'saved_filename': None,  # 사용자가 지정한 파일명
                'order': '',  # 차수 (파일명에서 파싱)
                'manufacturer': '',  # 제조사 (파일명에서 파싱)
                'model_name': '',  # 모델명 (파일명에서 파싱)
                'scenario': '',  # 시나리오명 (파일명에서 파싱)
                'memo': ''  # 메모 (사용자 입력)
            }
            
            # 아티팩트 데이터 저장
            if hasattr(self, 'artifact_data') and self.artifact_data:
                for artifact_id, data_list in self.artifact_data.items():
                    save_data['artifact_data'][artifact_id] = []
                    for data in data_list:
                        # 모든 필드를 JSON 직렬화 가능한 형태로 변환
                        item_data = {
                            'name': data.get('name'),
                            'path': data.get('path'),
                            'time': None,
                            'message': data.get('message'),
                            'is_kst': data.get('is_kst', False),
                            'original_time': data.get('original_time')
                        }
                        
                        # time 필드 처리
                        time_value = data.get('time')
                        if time_value:
                            if isinstance(time_value, datetime):
                                item_data['time'] = time_value.isoformat()
                            else:
                                item_data['time'] = str(time_value)
                        
                        # original_time도 datetime일 수 있으므로 변환
                        if item_data['original_time'] and isinstance(item_data['original_time'], datetime):
                            item_data['original_time'] = item_data['original_time'].isoformat()
                        
                        save_data['artifact_data'][artifact_id].append(item_data)
            else:
                self.log("[결과 저장] 아티팩트 데이터가 없습니다. 빈 결과로 저장합니다.")
            
            # Save deep search results
            if hasattr(self, 'deep_search_table') and self.deep_search_table:
                for row in range(self.deep_search_table.rowCount()):
                    search_time = self.deep_search_table.item(row, 0).text() if self.deep_search_table.item(row, 0) else ""
                    file_path = self.deep_search_table.item(row, 1).text() if self.deep_search_table.item(row, 1) else ""
                    match_format = self.deep_search_table.item(row, 2).text() if self.deep_search_table.item(row, 2) else ""
                    match_value = self.deep_search_table.item(row, 3).text() if self.deep_search_table.item(row, 3) else ""
                    save_data['deep_search_results'].append({
                        'search_time': search_time,
                        'file_path': file_path,
                        'match_format': match_format,
                        'match_value': match_value
                    })
            
            # 결과 디렉토리 생성
            results_dir = os.path.join(os.path.dirname(__file__), "saved_results")
            try:
                os.makedirs(results_dir, exist_ok=True)
                self.log(f"[결과 저장] 디렉토리 확인: {results_dir}")
            except Exception as e:
                self.log(f"[결과 저장] 디렉토리 생성 실패: {e}")
                return
            
            # 파일명 입력 받기 (포맷: N차 제조사 모델명 N번)
            memo_text = ''  # 메모 변수 초기화
            try:
                filename_dialog = QDialog(self)
                filename_dialog.setWindowTitle("결과 저장")
                filename_dialog.setMinimumWidth(500)
                
                layout = QVBoxLayout()
                filename_dialog.setLayout(layout)
                
                # 설명 레이블
                info_label = QLabel("파일명을 입력하세요 (포맷: N차 또는 ExN 제조사 모델명 시나리오명)\n예: 1차 삼성 SM-S921N 공장초기화 또는 Ex1 삼성 SM-S921N 공장초기화")
                info_label.setWordWrap(True)
                layout.addWidget(info_label)
                
                # 기존 데이터에서 선택할 수 있는 콤보박스 추가
                # 먼저 기존 데이터 로드
                existing_orders = set()
                existing_manufacturers = set()
                existing_models = set()
                existing_scenarios = set()
                
                try:
                    results_dir = os.path.join(os.path.dirname(__file__), "saved_results")
                    if os.path.exists(results_dir):
                        for filename in os.listdir(results_dir):
                            if not filename.endswith('.json'):
                                continue
                            try:
                                filepath = os.path.join(results_dir, filename)
                                with open(filepath, 'r', encoding='utf-8') as f:
                                    data = json.load(f)
                                saved_filename = data.get('saved_filename', filename)
                                display_name = saved_filename.replace('.json', '')
                                
                                # 파일명 파싱
                                parts = display_name.split()
                                if len(parts) >= 1:
                                    # Check for order pattern: "N차" or "ExN" format
                                    if '차' in parts[0] or (parts[0].startswith('Ex') and len(parts[0]) > 2 and parts[0][2:].isdigit()):
                                        existing_orders.add(parts[0])
                                        if len(parts) >= 2:
                                            existing_manufacturers.add(parts[1])
                                        if len(parts) >= 3:
                                            existing_models.add(parts[2])
                                        if len(parts) >= 4:
                                            existing_scenarios.add(' '.join(parts[3:]))
                            except:
                                continue
                except:
                    pass
                
                # 입력 필드들
                form_layout = QVBoxLayout()
                
                # 차수
                order_layout = QHBoxLayout()
                order_layout.addWidget(QLabel("차수:"))
                order_combo = QComboBox()
                order_combo.setEditable(True)
                order_combo.setInsertPolicy(QComboBox.NoInsert)
                order_combo.addItem("")
                order_combo.addItems(sorted(existing_orders))
                order_layout.addWidget(order_combo)
                form_layout.addLayout(order_layout)
                
                # 제조사
                manufacturer_layout = QHBoxLayout()
                manufacturer_layout.addWidget(QLabel("제조사:"))
                manufacturer_combo = QComboBox()
                manufacturer_combo.setEditable(True)
                manufacturer_combo.setInsertPolicy(QComboBox.NoInsert)
                manufacturer_combo.addItem("")
                manufacturer_combo.addItems(sorted(existing_manufacturers))
                manufacturer_layout.addWidget(manufacturer_combo)
                form_layout.addLayout(manufacturer_layout)
                
                # 모델명
                model_layout = QHBoxLayout()
                model_layout.addWidget(QLabel("Model:"))
                model_combo = QComboBox()
                model_combo.setEditable(True)
                model_combo.setInsertPolicy(QComboBox.NoInsert)
                model_combo.addItem("")
                model_combo.addItems(sorted(existing_models))
                model_layout.addWidget(model_combo)
                form_layout.addLayout(model_layout)
                
                # 시나리오명
                scenario_layout = QHBoxLayout()
                scenario_layout.addWidget(QLabel("시나리오명:"))
                scenario_combo = QComboBox()
                scenario_combo.setEditable(True)
                scenario_combo.setInsertPolicy(QComboBox.NoInsert)
                scenario_combo.addItem("")
                scenario_combo.addItems(sorted(existing_scenarios))
                scenario_layout.addWidget(scenario_combo)
                form_layout.addLayout(scenario_layout)
                
                layout.addLayout(form_layout)
                
                # 메모 입력 필드 추가
                memo_label = QLabel("메모 (선택사항):")
                memo_label.setStyleSheet("font-weight: bold;")
                layout.addWidget(memo_label)
                
                memo_edit = QTextEdit()
                memo_edit.setPlaceholderText("추가 메모를 입력하세요...")
                memo_edit.setMaximumHeight(80)
                layout.addWidget(memo_edit)
                
                # 미리보기
                preview_label = QLabel("파일명 미리보기:")
                preview_label.setStyleSheet("font-weight: bold;")
                layout.addWidget(preview_label)
                
                preview_text = QLineEdit()
                preview_text.setReadOnly(True)
                preview_text.setStyleSheet("background-color: #f0f0f0;")
                layout.addWidget(preview_text)
                
                def update_preview():
                    """미리보기 업데이트"""
                    try:
                        parts = []
                        if order_combo.currentText().strip():
                            parts.append(order_combo.currentText().strip())
                        if manufacturer_combo.currentText().strip():
                            parts.append(manufacturer_combo.currentText().strip())
                        if model_combo.currentText().strip():
                            parts.append(model_combo.currentText().strip())
                        if scenario_combo.currentText().strip():
                            parts.append(scenario_combo.currentText().strip())
                        
                        if parts:
                            preview = " ".join(parts)
                        else:
                            preview = "(입력 필요)"
                        preview_text.setText(preview)
                    except:
                        pass
                
                try:
                    order_combo.currentTextChanged.connect(update_preview)
                    manufacturer_combo.currentTextChanged.connect(update_preview)
                    model_combo.currentTextChanged.connect(update_preview)
                    scenario_combo.currentTextChanged.connect(update_preview)
                except:
                    pass
                
                # 버튼
                button_layout = QHBoxLayout()
                button_layout.addStretch()
                
                btn_cancel = QPushButton("취소")
                btn_cancel.clicked.connect(filename_dialog.reject)
                button_layout.addWidget(btn_cancel)
                
                btn_ok = QPushButton("저장")
                btn_ok.clicked.connect(filename_dialog.accept)
                btn_ok.setDefault(True)
                button_layout.addWidget(btn_ok)
                
                layout.addLayout(button_layout)
                
                # 다이얼로그 실행
                if filename_dialog.exec_() != QDialog.Accepted:
                    self.log("[결과 저장] 저장이 취소되었습니다.")
                    return
                
                # 파일명 조합
                parts = []
                try:
                    if order_combo.currentText().strip():
                        parts.append(order_combo.currentText().strip())
                    if manufacturer_combo.currentText().strip():
                        parts.append(manufacturer_combo.currentText().strip())
                    if model_combo.currentText().strip():
                        parts.append(model_combo.currentText().strip())
                    if scenario_combo.currentText().strip():
                        parts.append(scenario_combo.currentText().strip())
                except:
                    pass
                
                if not parts:
                    self.show_message("오류", "최소 하나의 필드는 입력해야 합니다.")
                    return
                
                filename = " ".join(parts)
                
                # 메모 가져오기
                try:
                    memo_text = memo_edit.toPlainText().strip()
                except:
                    memo_text = ''
            except Exception as e:
                import traceback
                error_msg = f"[파일명 입력 다이얼로그 오류] {str(e)}\n{traceback.format_exc()}"
                self.log(error_msg)
                # 오류 발생 시 기본 파일명 사용
                filename = f"analysis_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                self.log(f"[결과 저장] 오류로 인해 기본 파일명 사용: {filename}")
            
            # 확장자 처리
            if not filename.endswith('.json'):
                filename += '.json'
            
            # 저장 데이터에 파일명 추가
            save_data['saved_filename'] = filename
            
            # 파일명에서 정보 파싱하여 저장
            parts = filename.replace('.json', '').split()
            order = ''
            manufacturer = ''
            model_name = ''
            scenario = ''
            
            if len(parts) >= 1:
                # Check for order pattern: "N차" or "ExN" format
                if '차' in parts[0] or (parts[0].startswith('Ex') and len(parts[0]) > 2 and parts[0][2:].isdigit()):
                    order = parts[0]
                    if len(parts) >= 2:
                        manufacturer = parts[1]
                    if len(parts) >= 3:
                        model_name = parts[2]
                    if len(parts) >= 4:
                        scenario = ' '.join(parts[3:])
            
            save_data['order'] = order
            save_data['manufacturer'] = manufacturer
            save_data['model_name'] = model_name
            save_data['scenario'] = scenario
            
            # 메모 저장
            try:
                save_data['memo'] = memo_text
            except:
                save_data['memo'] = ''
            
            filepath = os.path.join(results_dir, filename)
            
            # 파일이 이미 존재하는지 확인
            if os.path.exists(filepath):
                reply = self.show_question("파일 존재", f"'{filename}' 파일이 이미 존재합니다.\n덮어쓰시겠습니까?")
                if reply != QMessageBox.Yes:
                    self.log("[결과 저장] 저장이 취소되었습니다.")
                    return
                # 덮어쓰기 확인만 하고 계속 진행
            
            self.log(f"[결과 저장] 저장 시도: {filepath}")
            
            # JSON 직렬화 가능한 형태로 변환 (모든 datetime 객체 처리)
            serializable_data = self._convert_to_json_serializable(save_data)
            
            # JSON 파일로 저장
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(serializable_data, f, ensure_ascii=False, indent=2)
            
            self.log(f"[결과 저장 성공] {filename}")
            self.log(f"[저장 위치] {results_dir}")
            
            # 저장된 결과 목록 새로고침 (예외 처리 추가)
            if hasattr(self, 'load_saved_results'):
                try:
                    self.load_saved_results()
                except Exception as load_error:
                    self.log(f"[결과 목록 새로고침 실패] {load_error}")
        except Exception as e:
            import traceback
            error_msg = f"[결과 저장 실패] {str(e)}\n{traceback.format_exc()}"
            self.log(error_msg)
            try:
                msg_box = CopyableMessageBox(self, "저장 오류", f"결과 저장 중 오류가 발생했습니다:\n{str(e)}")
                msg_box.exec_()
            except:
                pass  # GUI가 아직 준비되지 않았을 수 있음
    
    def show_saved_results(self):
        """저장된 결과 파일 탐색기 스타일로 보기 (별도 창)"""
        explorer = SavedResultsExplorer(self)
        explorer.exec_()
    
    def load_saved_results(self):
        """저장된 결과 목록 로드 (메인 화면 트리)"""
        try:
            if not hasattr(self, 'saved_results_tree') or not self.saved_results_tree:
                return
            
            self.saved_results_tree.clear()
            
            results_dir = os.path.join(os.path.dirname(__file__), "saved_results")
            if not os.path.exists(results_dir):
                try:
                    os.makedirs(results_dir, exist_ok=True)
                except:
                    pass
                return
            
            # 파일명 기반으로 그룹화 (차수/모델명 추출)
            file_list = []
            try:
                for filename in os.listdir(results_dir):
                    if not filename.endswith('.json'):
                        continue
                    
                    filepath = os.path.join(results_dir, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        # 사용자가 지정한 파일명 사용 (없으면 실제 파일명 사용)
                        saved_filename = data.get('saved_filename', filename)
                        display_name = saved_filename.replace('.json', '')
                        
                        # 파일명 파싱
                        parts = display_name.split()
                        order = '기타'
                        manufacturer = ''
                        model = ''
                        scenario = ''
                        
                        if len(parts) >= 1:
                            # Check for order pattern: "N차" or "ExN" format
                            if '차' in parts[0] or (parts[0].startswith('Ex') and len(parts[0]) > 2 and parts[0][2:].isdigit()):
                                order = parts[0]
                                remaining = parts[1:] if len(parts) > 1 else []
                            else:
                                remaining = parts
                            
                            if len(remaining) >= 3:
                                manufacturer = remaining[0]
                                model = remaining[1]
                                scenario = ' '.join(remaining[2:])
                            elif len(remaining) == 2:
                                manufacturer = remaining[0]
                                model = remaining[1]
                            elif len(remaining) == 1:
                                model = remaining[0]
                        
                        file_info = {
                            'filename': filename,
                            'filepath': filepath,
                            'data': data,
                            'display_name': display_name,
                            'order': order,
                            'manufacturer': manufacturer,
                            'model': model,
                            'scenario': scenario
                        }
                        file_list.append(file_info)
                        
                        # 디버깅: 첫 번째 파일 정보 로그
                        if len(file_list) == 1:
                            try:
                                self.log(f"[Filter] First file parsing result:")
                                self.log(f"  Filename: {display_name}")
                                self.log(f"  Order: '{order}', Manufacturer: '{manufacturer}', Model: '{model}', Scenario: '{scenario}'")
                            except:
                                pass
                    except Exception as e:
                        continue
            except Exception as e:
                return  # 디렉토리 읽기 실패 시 조용히 반환
            
            # 전체 데이터 저장 (필터링용)
            self.all_saved_results = file_list
            
            # 필터 콤보박스 업데이트 (안전하게)
            # 콤보박스가 초기화되었는지 확인
            try:
                has_order_combo = hasattr(self, 'filter_order_combo') and self.filter_order_combo is not None
                
                if has_order_combo:
                    self.update_filter_combos()
                else:
                    # QTimer를 사용하여 나중에 업데이트 시도
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(200, lambda: self._delayed_filter_update())
            except Exception as e:
                # Removed verbose combo box update error logging
                pass
            
            groups = self._build_saved_results_groups(file_list)
            
            # 트리 구성
            for order in self._sorted_orders(groups):
                order_item = QTreeWidgetItem(self.saved_results_tree)
                order_item.setText(0, order)
                order_item.setExpanded(True)
                
                for device_key in sorted(groups[order].keys()):
                    model_item = QTreeWidgetItem(order_item)
                    model_item.setText(0, f"{order} {device_key}".strip())
                    model_item.setExpanded(True)

                    scenario_groups = groups[order][device_key]
                    for scenario_key in self._sorted_scenario_keys(order, device_key, scenario_groups):
                        scenario_item = QTreeWidgetItem(model_item)
                        scenario_item.setText(0, scenario_key)
                        scenario_item.setExpanded(True)
                        for file_info in scenario_groups[scenario_key]:
                            result_item = QTreeWidgetItem(scenario_item)
                            result_item.setText(0, file_info['display_name'])
                            result_item.setData(0, Qt.UserRole, file_info['filepath'])
                            result_item.setData(0, Qt.UserRole + 1, file_info['data'])
        except Exception as e:
            import traceback
            error_msg = f"[Failed Load saved results] {str(e)}\n{traceback.format_exc()}"
            try:
                self.log(error_msg)
            except:
                pass  # log 함수가 없을 수 있음
    
    def update_filter_combos(self):
        """필터 콤보박스에 고유값 목록 업데이트"""
        try:
            if not hasattr(self, 'all_saved_results'):
                return
            
            if not self.all_saved_results:
                return
            
            # 고유값 추출
            unique_orders = set()
            unique_manufacturers = set()
            unique_models = set()
            unique_scenarios = set()
            
            for file_info in self.all_saved_results:
                try:
                    order = file_info.get('order', '').strip()
                    manufacturer = file_info.get('manufacturer', '').strip()
                    model = file_info.get('model', '').strip()
                    scenario = file_info.get('scenario', '').strip()
                    
                    if order:
                        unique_orders.add(order)
                    if manufacturer:
                        unique_manufacturers.add(manufacturer)
                    if model:
                        unique_models.add(model)
                    if scenario:
                        unique_scenarios.add(scenario)
                except Exception as e:
                    continue
            
            # 콤보박스 업데이트 (현재 선택값 유지)
            
            if hasattr(self, 'filter_order_combo') and self.filter_order_combo is not None:
                try:
                    current_order = self.filter_order_combo.currentText()
                    self.filter_order_combo.blockSignals(True)  # 시그널 차단
                    self.filter_order_combo.clear()
                    self.filter_order_combo.addItem("전체")
                    sorted_orders = sorted(unique_orders, key=self._order_sort_key)
                    self.filter_order_combo.addItems(sorted_orders)
                    # 이전 선택값 복원
                    index = self.filter_order_combo.findText(current_order)
                    if index >= 0:
                        self.filter_order_combo.setCurrentIndex(index)
                    elif current_order and current_order != "전체":
                        self.filter_order_combo.setEditText(current_order)
                    else:
                        self.filter_order_combo.setCurrentIndex(0)
                    self.filter_order_combo.blockSignals(False)  # 시그널 재개
                except Exception as e:
                    pass
            
            if hasattr(self, 'filter_manufacturer_combo') and self.filter_manufacturer_combo is not None:
                try:
                    current_manufacturer = self.filter_manufacturer_combo.currentText()
                    self.filter_manufacturer_combo.blockSignals(True)
                    self.filter_manufacturer_combo.clear()
                    self.filter_manufacturer_combo.addItem("전체")
                    self.filter_manufacturer_combo.addItems(sorted(unique_manufacturers))
                    index = self.filter_manufacturer_combo.findText(current_manufacturer)
                    if index >= 0:
                        self.filter_manufacturer_combo.setCurrentIndex(index)
                    elif current_manufacturer and current_manufacturer != "전체":
                        self.filter_manufacturer_combo.setEditText(current_manufacturer)
                    else:
                        self.filter_manufacturer_combo.setCurrentIndex(0)
                    self.filter_manufacturer_combo.blockSignals(False)
                except Exception as e:
                    pass
            
            if hasattr(self, 'filter_model_combo') and self.filter_model_combo is not None:
                try:
                    current_model = self.filter_model_combo.currentText()
                    self.filter_model_combo.blockSignals(True)
                    self.filter_model_combo.clear()
                    self.filter_model_combo.addItem("전체")
                    self.filter_model_combo.addItems(sorted(unique_models))
                    index = self.filter_model_combo.findText(current_model)
                    if index >= 0:
                        self.filter_model_combo.setCurrentIndex(index)
                    elif current_model and current_model != "전체":
                        self.filter_model_combo.setEditText(current_model)
                    else:
                        self.filter_model_combo.setCurrentIndex(0)
                    self.filter_model_combo.blockSignals(False)
                except Exception as e:
                    pass
            
            if hasattr(self, 'filter_scenario_combo') and self.filter_scenario_combo is not None:
                try:
                    current_scenario = self.filter_scenario_combo.currentText()
                    self.filter_scenario_combo.blockSignals(True)
                    self.filter_scenario_combo.clear()
                    self.filter_scenario_combo.addItem("전체")
                    self.filter_scenario_combo.addItems(sorted(unique_scenarios))
                    index = self.filter_scenario_combo.findText(current_scenario)
                    if index >= 0:
                        self.filter_scenario_combo.setCurrentIndex(index)
                    elif current_scenario and current_scenario != "전체":
                        self.filter_scenario_combo.setEditText(current_scenario)
                    else:
                        self.filter_scenario_combo.setCurrentIndex(0)
                    self.filter_scenario_combo.blockSignals(False)
                except Exception as e:
                    pass
        except Exception as e:
            # Removed verbose combo box update error logging
            pass
    
    def filter_saved_results(self):
        """저장된 결과 필터링"""
        try:
            if not hasattr(self, 'all_saved_results') or not self.all_saved_results:
                return
            
            filter_order, filter_manufacturer, filter_model, filter_scenario = self._get_current_saved_results_filter()
            filtered_list = self._filter_saved_results_list(self.all_saved_results)
            
            # 디버깅 로그
            try:
                self.log(f"[필터링] 전체: {len(self.all_saved_results)}개, 필터링 후: {len(filtered_list)}개")
                if filter_order or filter_manufacturer or filter_model or filter_scenario:
                    self.log(f"[필터링 조건] 차수: '{filter_order}', 제조사: '{filter_manufacturer}', Model: '{filter_model}', Scenario: '{filter_scenario}'")
            except:
                pass
            
            # 필터링된 목록으로 트리 업데이트
            self._update_saved_results_tree(filtered_list)
        except Exception as e:
            import traceback
            error_msg = f"[필터링 오류] {str(e)}\n{traceback.format_exc()}"
            try:
                self.log(error_msg)
            except:
                pass
    
    def clear_saved_results_filter(self):
        """저장된 결과 필터 초기화"""
        try:
            if hasattr(self, 'filter_order_combo'):
                self.filter_order_combo.setCurrentIndex(0)  # 빈 항목 선택
            if hasattr(self, 'filter_manufacturer_combo'):
                self.filter_manufacturer_combo.setCurrentIndex(0)
            if hasattr(self, 'filter_model_combo'):
                self.filter_model_combo.setCurrentIndex(0)
            if hasattr(self, 'filter_scenario_combo'):
                self.filter_scenario_combo.setCurrentIndex(0)
        except:
            pass
    
    def _update_saved_results_tree(self, file_list):
        """저장된 결과 트리 업데이트 (내부 메서드)"""
        try:
            if not hasattr(self, 'saved_results_tree') or not self.saved_results_tree:
                return
            
            self.saved_results_tree.clear()
            
            if not file_list:
                return
            
            groups = self._build_saved_results_groups(file_list)
            
            # 트리 구성
            for order in self._sorted_orders(groups):
                order_item = QTreeWidgetItem(self.saved_results_tree)
                order_item.setText(0, order)
                order_item.setExpanded(True)
                
                for device_key in sorted(groups[order].keys()):
                    model_item = QTreeWidgetItem(order_item)
                    model_item.setText(0, f"{order} {device_key}".strip())
                    model_item.setExpanded(True)

                    scenario_groups = groups[order][device_key]
                    for scenario_key in self._sorted_scenario_keys(order, device_key, scenario_groups):
                        scenario_item = QTreeWidgetItem(model_item)
                        scenario_item.setText(0, scenario_key)
                        scenario_item.setExpanded(True)
                        for file_info in scenario_groups[scenario_key]:
                            result_item = QTreeWidgetItem(scenario_item)
                            result_item.setText(0, file_info['display_name'])
                            result_item.setData(0, Qt.UserRole, file_info['filepath'])
                            result_item.setData(0, Qt.UserRole + 1, file_info['data'])
        except Exception as e:
            import traceback
            error_msg = f"[트리 업데이트 오류] {str(e)}\n{traceback.format_exc()}"
            try:
                self.log(error_msg)
            except:
                pass
    
    def _normalize_saved_source_code(self, source):
        """저장된 소스 값을 '1'/'2'/'3' 코드로 통일"""
        if source is None:
            return "1"
        source_str = str(source).strip()
        if source_str in ("1", "2", "3"):
            return source_str
        source_map = {"ZIP": "1", "ADB": "2", "Folder": "3", "zip": "1", "adb": "2", "folder": "3"}
        return source_map.get(source_str, "1")

    def _saved_source_label(self, source_code):
        return {"1": "ZIP", "2": "ADB", "3": "Folder"}.get(source_code, "ZIP")

    def _saved_source_needs_local_path(self, source_code):
        return source_code in ("1", "3")

    def _is_saved_local_path_valid(self, file_path, source_code):
        if not self._saved_source_needs_local_path(source_code):
            return True, ""
        if not file_path or not str(file_path).strip():
            return False, "원본 파일/폴더 경로가 비어 있습니다."
        path = os.path.abspath(str(file_path).strip())
        if source_code == "1":
            if not os.path.exists(path):
                return False, f"ZIP 파일이 존재하지 않습니다:\n{path}"
            if not zipfile.is_zipfile(path):
                return False, f"ZIP 파일이 아닙니다:\n{path}"
            return True, ""
        if not os.path.isdir(path):
            return False, f"폴더가 존재하지 않습니다:\n{path}"
        return True, ""

    def _update_saved_source_path_ui(self, data=None):
        """저장 결과 선택 시 원본 경로 UI 갱신"""
        if not hasattr(self, 'saved_source_path_edit'):
            return
        if not data:
            self.saved_source_status_label.setText("저장된 결과를 선택하면 원본 경로가 표시됩니다.")
            self.saved_source_path_edit.clear()
            self.saved_source_combo.setCurrentIndex(0)
            return

        source_code = self._normalize_saved_source_code(data.get('source', '1'))
        file_path = str(data.get('file_path', '') or '')
        source_index = self.saved_source_combo.findData(source_code)
        self.saved_source_combo.blockSignals(True)
        self.saved_source_combo.setCurrentIndex(source_index if source_index >= 0 else 0)
        self.saved_source_combo.blockSignals(False)
        self.saved_source_path_edit.setText(file_path if file_path and file_path != "N/A" else "")

        if self._saved_source_needs_local_path(source_code):
            valid, msg = self._is_saved_local_path_valid(file_path, source_code)
            if valid:
                self.saved_source_status_label.setText(
                    f"원본 경로 확인됨 ({self._saved_source_label(source_code)})."
                )
                self.saved_source_status_label.setStyleSheet("")
            else:
                self.saved_source_status_label.setText(
                    f"원본 경로를 찾을 수 없습니다. 이 PC에서 다시 지정하세요.\n{msg}"
                )
                self.saved_source_status_label.setStyleSheet("color: #c0392b;")
        else:
            self.saved_source_status_label.setText(
                "ADB 소스는 로컬 원본 파일 경로가 필요하지 않습니다."
            )
            self.saved_source_status_label.setStyleSheet("")

    def _on_saved_source_combo_changed(self):
        source_code = self.saved_source_combo.currentData()
        needs_path = self._saved_source_needs_local_path(source_code)
        self.saved_source_path_edit.setEnabled(needs_path)
        if not needs_path:
            self.saved_source_status_label.setText(
                "ADB 소스는 로컬 원본 파일 경로가 필요하지 않습니다."
            )
            self.saved_source_status_label.setStyleSheet("")

    def _rebuild_reset_instance_for_saved(self, source_code, file_path):
        """저장된 결과의 원본 경로로 deep search용 reset_instance 재생성"""
        artifacts = ["1", "21", "22", "3", "4", "5", "6", "7", "8", "9"]
        try:
            if getattr(self, 'reset_instance', None):
                zip_ref = getattr(self.reset_instance, 'zipref', None)
                if zip_ref:
                    try:
                        zip_ref.close()
                    except Exception:
                        pass
            self.reset_instance = ResetClassGUI(source_code, artifacts, file_path, self.result_text, self)
            if source_code == "1" and file_path:
                zip_ref = zipfile.ZipFile(file_path, 'r')
                self.reset_instance.file_list = zip_ref.namelist()
                self.reset_instance.zipref = zip_ref
                self.reset_instance.zipfile = file_path
                self.log(f"[원본 경로 적용] ZIP 파일 목록: {len(self.reset_instance.file_list)}개")
            elif source_code == "3" and file_path:
                self.reset_instance.file_list = self.reset_instance.collect_folder_files(file_path)
                self.reset_instance.base_path = file_path
                self.log(f"[원본 경로 적용] 폴더 파일 목록: {len(self.reset_instance.file_list)}개")
            return True, None
        except Exception as e:
            self.reset_instance = None
            return False, str(e)

    def browse_saved_source_path(self):
        """저장된 결과의 원본 ZIP/폴더 경로 선택"""
        try:
            source_code = self.saved_source_combo.currentData()
            if not self._saved_source_needs_local_path(source_code):
                self.show_message("안내", "ADB 소스는 로컬 원본 파일 경로를 선택하지 않습니다.")
                return
            current_path = self.saved_source_path_edit.text().strip()
            start_dir = current_path if current_path and os.path.exists(current_path) else ""
            if source_code == "1":
                selected, _ = QFileDialog.getOpenFileName(
                    self, "원본 ZIP 파일 선택", start_dir, "ZIP Files (*.zip);;All Files (*)",
                )
            else:
                selected = QFileDialog.getExistingDirectory(self, "원본 폴더 선택", start_dir)
            if selected:
                self.saved_source_path_edit.setText(selected)
        except Exception as e:
            self.show_message("오류", f"경로 선택 중 오류: {e}")

    def apply_saved_source_path(self):
        """입력한 원본 경로를 현재 세션에 적용"""
        if not self.current_saved_result_data:
            self.show_message("안내", "먼저 저장된 결과를 선택하세요.")
            return
        source_code = self.saved_source_combo.currentData()
        file_path = self.saved_source_path_edit.text().strip()
        if self._saved_source_needs_local_path(source_code):
            valid, msg = self._is_saved_local_path_valid(file_path, source_code)
            if not valid:
                self.show_message("경고", msg)
                return
            file_path = os.path.abspath(file_path)
        else:
            file_path = file_path or self.current_saved_result_data.get('file_path', '')

        self.saved_file_path = file_path
        self.saved_source = self._saved_source_label(source_code)
        self.current_saved_result_data['file_path'] = file_path
        self.current_saved_result_data['source'] = source_code

        ok, err = self._rebuild_reset_instance_for_saved(source_code, file_path)
        if not ok:
            self.show_message("경고", f"원본 경로 적용 중 오류:\n{err}")
            return

        self._update_saved_source_path_ui(self.current_saved_result_data)
        self.log(f"[원본 경로 적용] {self._saved_source_label(source_code)} - {file_path or 'N/A'}")
        self.show_message("완료", "원본 소스 경로가 적용되었습니다.\n상세 보기·Deep Search에서 원본 파일을 읽을 수 있습니다.")

    def persist_saved_source_path(self):
        """적용한 원본 경로를 저장 JSON 파일에 반영"""
        if not self.current_saved_result_filepath or not self.current_saved_result_data:
            self.show_message("안내", "먼저 저장된 결과를 선택하세요.")
            return
        source_code = self.saved_source_combo.currentData()
        file_path = self.saved_source_path_edit.text().strip()
        if self._saved_source_needs_local_path(source_code):
            valid, msg = self._is_saved_local_path_valid(file_path, source_code)
            if not valid:
                self.show_message("경고", msg)
                return
            file_path = os.path.abspath(file_path)

        self.current_saved_result_data['source'] = source_code
        self.current_saved_result_data['file_path'] = file_path
        self.saved_file_path = file_path
        self.saved_source = self._saved_source_label(source_code)

        ok, err = self._rebuild_reset_instance_for_saved(source_code, file_path)
        if not ok and self._saved_source_needs_local_path(source_code):
            self.show_message("경고", f"원본 경로 적용 중 오류:\n{err}")
            return

        try:
            serializable_data = self._convert_to_json_serializable(self.current_saved_result_data)
            with open(self.current_saved_result_filepath, 'w', encoding='utf-8') as f:
                json.dump(serializable_data, f, ensure_ascii=False, indent=2)
            self._update_saved_source_path_ui(self.current_saved_result_data)
            self.load_saved_results()
            self.log(f"[원본 경로 JSON 저장] {self.current_saved_result_filepath}")
            self.show_message("완료", "원본 소스 경로가 저장 파일에 반영되었습니다.")
        except Exception as e:
            self.show_message("오류", f"저장 중 오류가 발생했습니다:\n{e}")

    def on_saved_result_selected(self):
        """저장된 결과 선택 시"""
        try:
            selected = self.saved_results_tree.selectedItems()
            if not selected:
                return
            
            item = selected[0]
            data = item.data(0, Qt.UserRole + 1)
            if not data:
                return
            
            filepath = item.data(0, Qt.UserRole)
            # 선택된 결과를 현재 결과 영역에 로드
            self.load_saved_result_to_current(data, filepath)
        except Exception as e:
            try:
                self.log(f"[저장된 결과 선택 오류] {e}")
            except:
                pass
    
    def on_saved_result_double_clicked(self, item, column):
        """저장된 결과 더블 클릭 시 상세 정보 다이얼로그 표시"""
        try:
            # 선택된 항목의 데이터 가져오기
            data = item.data(0, Qt.UserRole + 1)
            if not data:
                return
            
            # SavedResultsExplorer 다이얼로그 열기
            explorer = SavedResultsExplorer(self)
            
            # 선택된 파일 경로와 데이터 설정
            filepath = item.data(0, Qt.UserRole)
            if filepath and data:
                explorer.current_filepath = filepath
                explorer.current_data = data
                explorer.display_result(data)
            
            explorer.exec_()
        except Exception as e:
            try:
                self.log(f"[저장된 결과 더블 클릭 오류] {e}")
            except:
                pass
    
    def load_saved_result_to_current(self, data, json_filepath=None):
        """저장된 결과를 현재 결과 영역에 로드"""
        try:
            self.current_saved_result_data = data
            self.current_saved_result_filepath = json_filepath
            self._update_saved_source_path_ui(data)

            # 저장된 파일 경로와 소스 정보 저장
            source_code = self._normalize_saved_source_code(data.get('source', '1'))
            file_path = data.get('file_path')
            self.saved_file_path = file_path
            self.saved_source = self._saved_source_label(source_code)

            path_missing = False
            if self._saved_source_needs_local_path(source_code):
                valid, path_msg = self._is_saved_local_path_valid(file_path, source_code)
                if not valid:
                    path_missing = True
                    self.log(f"[원본 경로 없음] {path_msg}")
            
            # 아티팩트 데이터 로드
            self.artifact_data = {}
            artifact_names = {
                "1": "bootstat",
                "2-1": "recovery.log",
                "21": "recovery.log",
                "2-2": "last_log",
                "22": "last_log",
                "3": "suggestions.xml",
                "4": "persistent_properties",
                "5": "appops",
                "6": "wellbing",
                "7": "internal",
                "8": "eRR.p",
                "9": "ULR_PERSISTENT_PREFS.xml"
            }
            
            for artifact_id, artifact_data_list in data.get('artifact_data', {}).items():
                self.artifact_data[artifact_id] = []
                for data_item in artifact_data_list:
                    # 시간 문자열을 datetime으로 변환
                    time_value = None
                    if data_item.get('time'):
                        try:
                            time_value = datetime.fromisoformat(data_item['time'])
                        except:
                            try:
                                time_value = datetime.fromtimestamp(float(data_item['time']))
                            except:
                                pass
                    
                    self.artifact_data[artifact_id].append({
                        'name': data_item.get('name'),
                        'path': data_item.get('path'),
                        'time': time_value,
                        'message': data_item.get('message'),
                        'is_kst': data_item.get('is_kst', False),
                        'original_time': data_item.get('original_time')
                    })
                
                # 테이블 업데이트
                if artifact_id in self.artifact_tables:
                    self.update_table(artifact_id, self.artifact_data[artifact_id])
            
            # Update summary results
            self.update_summary_table()
            self.run_multi_anchor_cross_validation()
            self.update_cross_validation_display()
            self.update_estimated_reset_time_display()
            
            # 필터링 적용
            self.apply_artifact_filter()
            
            # 확정 시간 로드
            confirmed_time = data.get('confirmed_time')
            if confirmed_time:
                self.confirmed_time_value = confirmed_time
                self.confirmed_time_dt = self.parse_time_text(confirmed_time)
                self.update_confirmed_time_display()
                self.apply_confirmed_time_highlight()
            
            # Load deep search results
            if hasattr(self, 'deep_search_table') and self.deep_search_table:
                self.deep_search_table.setRowCount(0)
                for result in data.get('deep_search_results', []):
                    row = self.deep_search_table.rowCount()
                    self.deep_search_table.insertRow(row)
                    self.deep_search_table.setItem(row, 0, QTableWidgetItem(result.get('search_time', '')))
                    self.deep_search_table.setItem(row, 1, QTableWidgetItem(result.get('file_path', '')))
                    self.deep_search_table.setItem(row, 2, QTableWidgetItem(result.get('match_format', '')))
                    self.deep_search_table.setItem(row, 3, QTableWidgetItem(result.get('match_value', '')))
            
            # 탭 순서 재정렬
            self.reorder_tabs()
            
            # Set reset_instance (needed for deep search)
            if not path_missing and file_path and source_code:
                ok, err = self._rebuild_reset_instance_for_saved(source_code, file_path)
                if not ok:
                    self.log(f"[경고] reset_instance 생성 실패: {err}")
            elif path_missing:
                self.reset_instance = None
            
            # Enable deep search button (if data exists)
            if hasattr(self, 'btn_deep_search') and self.btn_deep_search:
                if self.artifact_data and any(self.artifact_data.values()):
                    self.btn_deep_search.setEnabled(True)
                else:
                    self.btn_deep_search.setEnabled(False)
            
            # 로그 메시지
            self.log(f"[Load saved results] {data.get('timestamp', 'N/A')} - {data.get('file_path', 'N/A')}")

            if path_missing:
                self.show_message(
                    "원본 경로 필요",
                    "저장된 원본 파일/폴더 경로를 이 PC에서 찾을 수 없습니다.\n"
                    "오른쪽 '원본 소스 경로'에서 소스와 경로를 다시 지정한 뒤 "
                    "'경로 적용' 또는 'JSON 저장'을 눌러 주세요.",
                )
        except Exception as e:
            import traceback
            error_msg = f"[저장된 결과 로드 오류] {str(e)}\n{traceback.format_exc()}"
            try:
                self.log(error_msg)
            except Exception:
                pass
    
    def _get_current_saved_results_filter(self):
        """현재 필터 콤보박스 값 반환 (order, manufacturer, model, scenario)"""
        filter_order = ''
        filter_manufacturer = ''
        filter_model = ''
        filter_scenario = ''
        try:
            if hasattr(self, 'filter_order_combo') and self.filter_order_combo is not None:
                filter_order = self._normalized_filter_text(self.filter_order_combo)
        except Exception:
            pass
        try:
            if hasattr(self, 'filter_manufacturer_combo') and self.filter_manufacturer_combo is not None:
                filter_manufacturer = self._normalized_filter_text(self.filter_manufacturer_combo)
        except Exception:
            pass
        try:
            if hasattr(self, 'filter_model_combo') and self.filter_model_combo is not None:
                filter_model = self._normalized_filter_text(self.filter_model_combo)
        except Exception:
            pass
        try:
            if hasattr(self, 'filter_scenario_combo') and self.filter_scenario_combo is not None:
                filter_scenario = self._normalized_filter_text(self.filter_scenario_combo)
        except Exception:
            pass
        return filter_order, filter_manufacturer, filter_model, filter_scenario

    def _filter_saved_results_list(self, file_list):
        """file_list에 현재 필터 조건 적용"""
        filter_order, filter_manufacturer, filter_model, filter_scenario = self._get_current_saved_results_filter()
        filtered_list = []
        for file_info in file_list:
            order = str(file_info.get('order', '')).strip().lower()
            manufacturer = str(file_info.get('manufacturer', '')).strip().lower()
            model = str(file_info.get('model', '')).strip().lower()
            scenario = str(file_info.get('scenario', '')).strip().lower()

            match = True
            if filter_order and filter_order not in order:
                match = False
            if match and filter_manufacturer and filter_manufacturer not in manufacturer:
                match = False
            if match and filter_model and filter_model not in model:
                match = False
            if match and filter_scenario and filter_scenario not in scenario:
                match = False
            if match:
                filtered_list.append(file_info)
        return filtered_list

    def _collect_saved_results_from_tree_item(self, item):
        """트리 항목(하위 포함)에서보낼 file_info 목록 수집"""
        collected = []
        filepath = item.data(0, Qt.UserRole)
        data = item.data(0, Qt.UserRole + 1)
        if filepath and data:
            display_name = data.get('saved_filename', os.path.basename(filepath))
            if display_name.endswith('.json'):
                display_name = display_name[:-5]
            order, manufacturer, model, scenario = self._parse_saved_display_name(display_name)
            collected.append({
                'filename': os.path.basename(filepath),
                'filepath': filepath,
                'data': data,
                'display_name': display_name,
                'order': order,
                'manufacturer': manufacturer,
                'model': model,
                'scenario': scenario,
            })
        for i in range(item.childCount()):
            collected.extend(self._collect_saved_results_from_tree_item(item.child(i)))
        return collected

    def _parse_saved_display_name(self, display_name):
        """저장 파일 표시명에서 차수/제조사/모델/시나리오 파싱"""
        parts = str(display_name or '').split()
        order = '기타'
        manufacturer = ''
        model = ''
        scenario = ''
        if not parts:
            return order, manufacturer, model, scenario
        if '차' in parts[0] or (parts[0].startswith('Ex') and len(parts[0]) > 2 and parts[0][2:].isdigit()):
            order = parts[0]
            remaining = parts[1:]
        else:
            remaining = parts
        if len(remaining) >= 3:
            manufacturer, model = remaining[0], remaining[1]
            scenario = ' '.join(remaining[2:])
        elif len(remaining) == 2:
            manufacturer, model = remaining[0], remaining[1]
        elif len(remaining) == 1:
            model = remaining[0]
        return order, manufacturer, model, scenario

    def _merge_file_info_lists(self, lists):
        """filepath 기준 중복 제거 병합"""
        merged = []
        seen = set()
        for file_list in lists:
            for file_info in file_list:
                filepath = file_info.get('filepath')
                if not filepath or filepath in seen:
                    continue
                seen.add(filepath)
                merged.append(file_info)
        return merged

    def _get_saved_results_for_export(self, scope):
        """보내기 범위에 따른 file_info 목록"""
        if scope == 'all':
            return list(getattr(self, 'all_saved_results', []) or [])
        if scope == 'filtered':
            base = getattr(self, 'all_saved_results', []) or []
            return self._filter_saved_results_list(base)
        if scope == 'selected':
            if not hasattr(self, 'saved_results_tree'):
                return []
            selected_items = self.saved_results_tree.selectedItems()
            if not selected_items:
                return []
            collected = []
            for item in selected_items:
                collected.extend(self._collect_saved_results_from_tree_item(item))
            # all_saved_results에 있는 항목은 메타데이터 보강
            by_path = {fi.get('filepath'): fi for fi in (getattr(self, 'all_saved_results', []) or [])}
            enriched = []
            for file_info in collected:
                filepath = file_info.get('filepath')
                if filepath in by_path:
                    enriched.append(by_path[filepath])
                else:
                    enriched.append(file_info)
            return self._merge_file_info_lists([enriched])
        return []

    def _format_saved_time_str(self, time_value, is_kst=False):
        """저장 JSON의 시간 값을 화면 표시 형식 문자열로 변환"""
        if not time_value:
            return ''
        dt = None
        if isinstance(time_value, datetime):
            dt = time_value
        elif isinstance(time_value, str):
            try:
                dt = datetime.fromisoformat(time_value)
            except Exception:
                try:
                    dt = datetime.fromtimestamp(float(time_value))
                except Exception:
                    return time_value
        if not dt:
            return str(time_value)
        _, time_str = self.format_time_for_display(dt, is_kst)
        return time_str

    def _saved_source_label_from_data(self, data):
        source_code = self._normalize_saved_source_code(data.get('source', '1'))
        return self._saved_source_label(source_code)

    def _build_export_tables_from_saved_results(self, file_list):
        """저장 결과 목록을 보내기용 표 데이터로 변환"""
        artifact_names = dict(self.artifact_names) if hasattr(self, 'artifact_names') else {
            "1": "bootstat",
            "21": "recovery.log",
            "22": "last_log",
            "3": "suggestions.xml",
            "4": "persistent_properties",
            "5": "appops",
            "6": "wellbing",
            "7": "internal",
            "8": "eRR.p",
            "9": "ULR_PERSISTENT_PREFS.xml",
        }

        summary_rows = []
        detail_rows = []
        deep_rows = []

        for file_info in file_list:
            data = file_info.get('data') or {}
            display_name = file_info.get('display_name', '')
            order = file_info.get('order', '')
            manufacturer = file_info.get('manufacturer', '')
            model = file_info.get('model', '')
            scenario = file_info.get('scenario', '')

            summary_rows.append({
                '차수': order,
                '제조사': manufacturer,
                '모델': model,
                '시나리오': scenario,
                '결과명': display_name,
                '저장시각': data.get('timestamp', ''),
                '확정 초기화 시간': data.get('confirmed_time', '') or '',
                '메모': data.get('memo', '') or '',
                '원본 소스': self._saved_source_label_from_data(data),
                '원본 경로': data.get('file_path', '') or '',
                'JSON 파일': file_info.get('filepath', ''),
            })

            for artifact_id, artifact_data_list in (data.get('artifact_data') or {}).items():
                artifact_name = artifact_names.get(str(artifact_id), str(artifact_id))
                for item in artifact_data_list or []:
                    time_str = self._format_saved_time_str(item.get('time'), item.get('is_kst', False))
                    original_time = item.get('original_time')
                    if isinstance(original_time, datetime):
                        original_time_str = original_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        original_time_str = str(original_time) if original_time else time_str
                    detail_rows.append({
                        '차수': order,
                        '제조사': manufacturer,
                        '모델': model,
                        '시나리오': scenario,
                        '결과명': display_name,
                        'Artifact': artifact_name,
                        'Item': item.get('name', ''),
                        'Path': item.get('path', ''),
                        'Time': time_str,
                        'Original Time': original_time_str,
                        'Message': item.get('message', '') or '',
                    })

            for result in data.get('deep_search_results') or []:
                deep_rows.append({
                    '차수': order,
                    '제조사': manufacturer,
                    '모델': model,
                    '시나리오': scenario,
                    '결과명': display_name,
                    'Search Time': result.get('search_time', ''),
                    'File Path': result.get('file_path', ''),
                    'Match Format': result.get('match_format', ''),
                    'Match Value': result.get('match_value', ''),
                })

        return {
            '요약': pd.DataFrame(summary_rows),
            '상세(시간)': pd.DataFrame(detail_rows),
            'Deep Search': pd.DataFrame(deep_rows),
        }

    def show_export_saved_results_dialog(self):
        """저장된 결과 일괄 보내기 다이얼로그"""
        if not getattr(self, 'all_saved_results', None):
            self.load_saved_results()
        if not self.all_saved_results:
            self.show_message("안내", "보낼 저장 결과가 없습니다.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("저장 결과 일괄 보내기")
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)

        info = QLabel(
            "저장된 분석 결과를 한 파일(또는 폴더)로 정리해보냅니다.\n"
            "요약·상세(시간)·Deep Search 시트/CSV가 포함됩니다."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        scope_group = QGroupBox("보내기 범위")
        scope_layout = QVBoxLayout(scope_group)
        scope_all = QRadioButton("전체 저장 결과")
        scope_filtered = QRadioButton("현재 필터에 맞는 결과")
        scope_selected = QRadioButton("트리에서 선택한 항목 (하위 포함)")
        scope_filtered.setChecked(True)
        scope_buttons = QButtonGroup(dialog)
        scope_buttons.addButton(scope_all, 0)
        scope_buttons.addButton(scope_filtered, 1)
        scope_buttons.addButton(scope_selected, 2)
        scope_layout.addWidget(scope_all)
        scope_layout.addWidget(scope_filtered)
        scope_layout.addWidget(scope_selected)
        layout.addWidget(scope_group)

        format_group = QGroupBox("파일 형식")
        format_layout = QVBoxLayout(format_group)
        fmt_excel = QRadioButton("Excel 통합 (.xlsx)")
        fmt_csv = QRadioButton("CSV 폴더 (요약·상세·Deep Search)")
        fmt_zip = QRadioButton("ZIP (통합 Excel + JSON 원본)")
        fmt_excel.setChecked(True)
        format_buttons = QButtonGroup(dialog)
        format_buttons.addButton(fmt_excel, 0)
        format_buttons.addButton(fmt_csv, 1)
        format_buttons.addButton(fmt_zip, 2)
        format_layout.addWidget(fmt_excel)
        format_layout.addWidget(fmt_csv)
        format_layout.addWidget(fmt_zip)
        layout.addWidget(format_group)

        count_label = QLabel("")
        count_label.setObjectName("subtleText")

        def refresh_count():
            scope_id = scope_buttons.checkedId()
            scope_map = {0: 'all', 1: 'filtered', 2: 'selected'}
            file_list = self._get_saved_results_for_export(scope_map.get(scope_id, 'filtered'))
            count_label.setText(f"대상: {len(file_list)}건")

        scope_all.toggled.connect(refresh_count)
        scope_filtered.toggled.connect(refresh_count)
        scope_selected.toggled.connect(refresh_count)
        refresh_count()
        layout.addWidget(count_label)

        btn_layout = QHBoxLayout()
        btn_export = QPushButton("보내기")
        btn_export.setObjectName("primaryButton")
        btn_cancel = QPushButton("취소")
        btn_layout.addWidget(btn_export)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        def do_export():
            scope_map = {0: 'all', 1: 'filtered', 2: 'selected'}
            fmt_map = {0: 'excel', 1: 'csv', 2: 'zip'}
            scope = scope_map.get(scope_buttons.checkedId(), 'filtered')
            fmt = fmt_map.get(format_buttons.checkedId(), 'excel')
            file_list = self._get_saved_results_for_export(scope)
            if not file_list:
                self.show_message("안내", "보낼 결과가 없습니다. 범위를 확인하세요.")
                return
            try:
                if fmt == 'excel':
                    default_name = f"saved_results_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    path, _ = QFileDialog.getSaveFileName(
                        dialog, "Excel로 저장", default_name, "Excel Files (*.xlsx)"
                    )
                    if not path:
                        return
                    self._export_saved_results_to_excel(file_list, path)
                elif fmt == 'csv':
                    folder = QFileDialog.getExistingDirectory(dialog, "CSV 저장 폴더 선택")
                    if not folder:
                        return
                    self._export_saved_results_to_csv_folder(file_list, folder)
                else:
                    default_name = f"saved_results_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                    path, _ = QFileDialog.getSaveFileName(
                        dialog, "ZIP으로 저장", default_name, "ZIP Files (*.zip)"
                    )
                    if not path:
                        return
                    self._export_saved_results_to_zip(file_list, path)
                dialog.accept()
            except Exception as e:
                import traceback
                self.show_message("오류", f"보내기 중 오류가 발생했습니다:\n{e}\n{traceback.format_exc()}")

        btn_export.clicked.connect(do_export)
        btn_cancel.clicked.connect(dialog.reject)
        dialog.exec_()

    def _write_export_tables_to_excel(self, tables, path):
        """보내기 표 데이터를 Excel 파일로 기록 (UI 없음)"""
        if not path.lower().endswith('.xlsx'):
            path += '.xlsx'
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            for sheet_name, df in tables.items():
                safe_name = sheet_name[:31]
                if df.empty:
                    pd.DataFrame([{'안내': '데이터 없음'}]).to_excel(writer, sheet_name=safe_name, index=False)
                else:
                    df.to_excel(writer, sheet_name=safe_name, index=False)
        return path

    def _write_export_tables_to_csv_folder(self, tables, folder, file_count):
        """보내기 표 데이터를 CSV 폴더로 기록 (UI 없음)"""
        os.makedirs(folder, exist_ok=True)
        name_map = {
            '요약': '00_summary.csv',
            '상세(시간)': '01_artifact_times.csv',
            'Deep Search': '02_deep_search.csv',
        }
        for sheet_name, df in tables.items():
            filename = name_map.get(sheet_name, f"{sheet_name}.csv")
            out_path = os.path.join(folder, filename)
            if df.empty:
                pd.DataFrame([{'안내': '데이터 없음'}]).to_csv(out_path, index=False, encoding='utf-8-sig')
            else:
                df.to_csv(out_path, index=False, encoding='utf-8-sig')
        readme_path = os.path.join(folder, 'README.txt')
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(
                f"저장 결과 일괄 보내기\n"
                f"생성 시각: {datetime.now().isoformat()}\n"
                f"포함 건수: {file_count}\n"
            )
        return folder

    def _export_saved_results_to_excel(self, file_list, path):
        """Excel 통합 파일로 저장"""
        tables = self._build_export_tables_from_saved_results(file_list)
        try:
            path = self._write_export_tables_to_excel(tables, path)
        except ImportError:
            base, _ = os.path.splitext(path)
            folder = base + '_csv'
            self._write_export_tables_to_csv_folder(tables, folder, len(file_list))
            self.show_message(
                "안내",
                "openpyxl이 없어 Excel 대신 CSV 폴더로 저장했습니다.\n"
                f"경로: {folder}\n\n"
                "Excel 저장을 원하면: pip install openpyxl",
            )
            return
        self.log(f"[일괄 보내기] Excel 저장: {path} ({len(file_list)}건)")
        self.show_message("완료", f"Excel 파일로 저장했습니다.\n{path}\n\n포함: {len(file_list)}건")

    def _export_saved_results_to_csv_folder(self, file_list, folder):
        """CSV 폴더로 저장"""
        tables = self._build_export_tables_from_saved_results(file_list)
        folder = self._write_export_tables_to_csv_folder(tables, folder, len(file_list))
        self.log(f"[일괄 보내기] CSV 폴더 저장: {folder} ({len(file_list)}건)")
        self.show_message("완료", f"CSV 폴더로 저장했습니다.\n{folder}\n\n포함: {len(file_list)}건")

    def _export_saved_results_to_zip(self, file_list, path):
        """ZIP: 통합 Excel + JSON 원본(차수/기기별 폴더)"""
        if not path.lower().endswith('.zip'):
            path += '.zip'
        tables = self._build_export_tables_from_saved_results(file_list)
        with tempfile.TemporaryDirectory() as tmpdir:
            excel_path = os.path.join(tmpdir, 'saved_results_summary.xlsx')
            try:
                self._write_export_tables_to_excel(tables, excel_path)
            except Exception:
                self._write_export_tables_to_csv_folder(tables, os.path.join(tmpdir, 'csv'), len(file_list))
            json_root = os.path.join(tmpdir, 'json')
            for file_info in file_list:
                src = file_info.get('filepath')
                if not src or not os.path.isfile(src):
                    continue
                order = file_info.get('order', '기타') or '기타'
                manufacturer = file_info.get('manufacturer', '').strip()
                model = file_info.get('model', '').strip()
                scenario = file_info.get('scenario', '').strip() or '시나리오없음'
                device_folder = f"{manufacturer}_{model}".strip('_') or model or '기타'
                dest_dir = os.path.join(json_root, order, device_folder, scenario)
                os.makedirs(dest_dir, exist_ok=True)
                dest_file = os.path.join(dest_dir, os.path.basename(src))
                if os.path.exists(dest_file):
                    base, ext = os.path.splitext(os.path.basename(src))
                    dest_file = os.path.join(dest_dir, f"{base}_{datetime.now().strftime('%H%M%S')}{ext}")
                shutil.copy2(src, dest_file)
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(tmpdir):
                    for name in files:
                        full = os.path.join(root, name)
                        arcname = os.path.relpath(full, tmpdir)
                        zf.write(full, arcname)
        self.log(f"[일괄 보내기] ZIP 저장: {path} ({len(file_list)}건)")
        self.show_message("완료", f"ZIP 파일로 저장했습니다.\n{path}\n\n포함: {len(file_list)}건")

    def delete_saved_result(self):
        """선택된 저장 결과 삭제"""
        selected = self.saved_results_tree.selectedItems()
        if not selected:
            self.show_message("경고", "삭제할 결과를 선택하세요.")
            return
        
        item = selected[0]
        filepath = item.data(0, Qt.UserRole)
        
        if not filepath:
            self.show_message("경고", "유효하지 않은 선택입니다.")
            return
        
        reply = self.show_question("확인", "선택한 결과를 삭제하시겠습니까?")
        if reply == QMessageBox.Yes:
            try:
                os.remove(filepath)
                self.load_saved_results()  # 목록 새로고침
                self.show_message("완료", "삭제되었습니다.")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"삭제 중 오류가 발생했습니다:\n{e}")


class ResetClassGUI:
    """GUI 버전의 ResetClass - print 대신 QTextEdit에 출력 및 표에 데이터 추가"""
    def __init__(self, choice, artifact_choices, file_path, output_widget, gui_instance=None):
        self.choice = choice
        self.artifact_choices = artifact_choices if isinstance(artifact_choices, list) else [artifact_choices]
        self.file_path = file_path
        self.output_widget = output_widget
        self.gui_instance = gui_instance  # FactoryResetGUI 인스턴스
        self.zipfile = None
        self.zipref = None
        self.base_path = None
        self.file_list = []
        self.adb_device_id = None  # 여러 디바이스가 있을 때 사용할 디바이스 ID
        self.last_abx_output = None
        
        # 로그 파일 설정
        self.log_file = None
        self.setup_logging()
    
    def setup_logging(self):
        """파일 로깅 설정"""
        try:
            log_dir = os.path.join(os.path.dirname(__file__), "logs")
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = os.path.join(log_dir, f"analysis_{timestamp}.log")
            self.log_file = open(log_filename, 'w', encoding='utf-8')
            self.log_to_file(f"[로그 파일 생성] {log_filename}")
        except Exception as e:
            # 로그 파일 생성 실패해도 계속 진행
            pass
    
    def log_to_file(self, message):
        """파일에만 기록 (GUI 출력 없이)"""
        try:
            if self.log_file:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.log_file.write(f"[{timestamp}] {message}\n")
                self.log_file.flush()  # 즉시 디스크에 쓰기
        except Exception:
            pass
    
    def log(self, message):
        """출력 메서드 - QTextEdit에 텍스트 추가 및 파일에 기록"""
        try:
            # GUI 출력은 항상 GUI 인스턴스의 thread-safe 메서드를 사용
            if self.gui_instance and hasattr(self.gui_instance, "log"):
                self.gui_instance.log(message)
            elif self.output_widget:
                self.output_widget.append(message)
            
            # 파일에 기록
            self.log_to_file(message)
        except Exception:
            # 로깅 실패해도 계속 진행
            pass
    
    def log_error(self, message, exception=None):
        """에러 로깅 (상세 정보 포함)"""
        error_msg = f"[ERROR] {message}"
        if exception:
            error_msg += f"\n{str(exception)}"
            error_msg += f"\n{traceback.format_exc()}"
        self.log(error_msg)
    
    def log_performance(self, operation, duration):
        """성능 로깅"""
        self.log(f"[PERFORMANCE] {operation}: {duration:.2f}초")

    def __del__(self):
        """소멸자 - 로그 파일 닫기"""
        try:
            if self.log_file:
                self.log_file.close()
        except:
            pass
    
    def run_analysis(self):
        """분석 실행"""
        start_time = datetime.now()
        self.log(f"[분석 시작] 모드: {self.choice}, 아티팩트: {self.artifact_choices}")
        
        try:
            if self.choice == "1":
                # ZIP 파일 모드
                if not self.file_path:
                    self.log("파일이 선택되지 않았습니다.")
                    return

                self.zipfile = self.file_path
                self.log(f"[#] zip 파일 경로 : {self.file_path}")

                try:
                    zip_start = datetime.now()
                    with zipfile.ZipFile(self.file_path, 'r') as zip_ref:
                        self.zipref = zip_ref
                        self.file_list = zip_ref.namelist()
                    zip_duration = (datetime.now() - zip_start).total_seconds()
                    self.log_performance("ZIP 파일 열기", zip_duration)
                    self.log(f"[ZIP 파일] 파일 수: {len(self.file_list)}")
                except Exception as e:
                    self.log_error("ZIP 파일 열기 실패", e)
                    return

                try:
                    user_id = self.get_user_path()
                    self.base_path = None
                    process_start = datetime.now()
                    self.process_artifacts_zip(user_id)
                    process_duration = (datetime.now() - process_start).total_seconds()
                    self.log_performance("아티팩트 처리", process_duration)
                except Exception as e:
                    self.log_error("아티팩트 처리 중 오류", e)
                    raise

            elif self.choice == "2":
                # ADB 모드
                self.log("[#] ADB 모드로 실행합니다.")

                try:
                    # ADB 연결 확인
                    if not self.check_adb_connection():
                        self.log("오류: ADB 연결을 확인할 수 없습니다.")
                        self.log("USB 디버깅이 활성화되어 있고 디바이스가 연결되어 있는지 확인하세요.")
                        return

                    # 루트 권한 확인
                    if not self.check_root_access():
                        self.log("경고: 루트 권한이 없습니다.")
                        self.log("일부 파일에 접근할 수 없을 수 있습니다.")
                        self.log("루트 권한이 필요한 경우 디바이스를 루팅하거나 su 명령을 허용하세요.")
                        # 경고만 표시하고 계속 진행

                    user_id = self.get_user_path()
                    process_start = datetime.now()
                    self.process_artifacts_adb(user_id)
                    process_duration = (datetime.now() - process_start).total_seconds()
                    self.log_performance("ADB 아티팩트 처리", process_duration)
                except Exception as e:
                    self.log_error("ADB 모드 처리 중 오류", e)
                    raise

            elif self.choice == "3":
                # Folder mode
                if not self.file_path:
                    self.log("Folder not selected.")
                    return

                try:
                    self.base_path = self.file_path
                    self.zipfile = None
                    self.zipref = None
                    self.log(f"[#] Folder path: {self.file_path}")
                    
                    collect_start = datetime.now()
                    self.file_list = self.collect_folder_files(self.file_path)
                    collect_duration = (datetime.now() - collect_start).total_seconds()
                    self.log_performance("Folder file collection", collect_duration)
                    self.log(f"[Folder] File count: {len(self.file_list)}")
            
                    user_id = self.get_user_path()
                    process_start = datetime.now()
                    self.process_artifacts_folder(user_id)
                    process_duration = (datetime.now() - process_start).total_seconds()
                    self.log_performance("폴더 아티팩트 처리", process_duration)
                except Exception as e:
                    self.log_error("폴더 모드 처리 중 오류", e)
                    raise
            else:
                self.log("Invalid choice. Exiting.")
        except Exception as e:
            self.log_error("분석 실행 중 치명적 오류", e)
            raise
        finally:
            total_duration = (datetime.now() - start_time).total_seconds()
            self.log_performance("전체 분석", total_duration)
            self.log(f"[분석 완료] 총 소요 시간: {total_duration:.2f}초")

    def should_process_artifact(self, artifact_id):
        """아티팩트를 처리해야 하는지 확인"""
        return "0" in self.artifact_choices or artifact_id in self.artifact_choices

    def _bootstat_target_paths(self, filename):
        if self.choice == "2":
            return [f"/data/misc/bootstat/{filename}"]
        return [f"Dump/data/misc/bootstat/{filename}"]

    def process_bootstat(self):
        """bootstat 5종 처리 + 동시 갱신 쌍 진단 (논문 §4.1)"""
        bootstat_specs = [
            ("factory_reset", "T1 직접"),
            ("factory_reset_record_value", "T1 직접 (동시갱신쌍)"),
            ("factory_reset_current_time", "T2 보조 (최근 부팅)"),
            ("last_boot_time_utc", "T2 보조 (최근 부팅)"),
            ("build_date", "진단 (ROM 빌드)"),
        ]
        results = {}
        found_any = False
        self.log("******************************************")
        self.log("[1] bootstat 영역 (5종)")
        for filename, tier_label in bootstat_specs:
            matchtime = None
            found_path = None
            for target in self._bootstat_target_paths(filename):
                if self._file_exists_by_mode(target):
                    found_path = target
                    matchtime = self.get_mod_time_from_zip(target)
                    if matchtime:
                        break
            results[filename] = {'time': matchtime, 'path': found_path}
            if matchtime:
                found_any = True
                self.timestamp_process(
                    matchtime,
                    artifact_id="1",
                    path=found_path or self._bootstat_target_paths(filename)[0],
                    name=filename,
                    original_time=tier_label,
                    is_kst=True,
                )
                self.log(f"  {filename}: {matchtime} ({tier_label})")
            else:
                self.log(f"  {filename}: 없음 또는 시간 없음")
        self.log("******************************************\n")

        fr = results.get('factory_reset', {}).get('time')
        frv = results.get('factory_reset_record_value', {}).get('time')
        if fr and frv and self.gui_instance:
            delta = abs((fr - frv).total_seconds())
            if delta <= 5:
                msg = f"동시 갱신 쌍 일치 (|Δ|={delta:.0f}초)"
            else:
                msg = f"동시 갱신 쌍 불일치 (|Δ|={delta:.0f}초) — 위·변조/OEM 의심"
            self.gui_instance.add_artifact_data(
                "1",
                "bootstat (동시 갱신 쌍 검증)",
                results['factory_reset'].get('path', ''),
                fr,
                msg,
                is_kst=True,
                original_time=f"factory_reset={fr}, record_value={frv}",
            )
        elif not found_any and self.gui_instance:
            self.gui_instance.add_artifact_data(
                "1",
                "factory_reset",
                self._bootstat_target_paths("factory_reset")[0],
                None,
                "bootstat 파일이 존재하지 않거나 시간 정보가 없습니다.",
            )

    def process_artifacts_zip(self, user_id):
        """ZIP 모드에서 아티팩트 처리"""
        try:
            # artifact 1: bootstat (5종 + 동시 갱신 쌍)
            if self.should_process_artifact("1"):
                try:
                    self.process_bootstat()
                except Exception as e:
                    self.log_error("bootstat 처리 중 오류", e)

            # artifact 2-1: recovery.log
            if self.should_process_artifact("21") or self.should_process_artifact("2-1"):
                try:
                    self.process_recovery_log_zip()
                except Exception as e:
                    self.log_error("recovery.log 처리 중 오류", e)

            # artifact 2-2: last_log
            if self.should_process_artifact("22") or self.should_process_artifact("2-2"):
                try:
                    self.process_last_log_zip()
                except Exception as e:
                    self.log_error("last_log 처리 중 오류", e)

            # artifact 3: suggestions.xml
            if self.should_process_artifact("3"):
                try:
                    self.process_suggestions_zip(user_id)
                except Exception as e:
                    self.log_error("suggestions.xml 처리 중 오류", e)

            # artifact 4: persistent_properties
            if self.should_process_artifact("4"):
                try:
                    self.process_persistent_properties_zip()
                except Exception as e:
                    self.log_error("persistent_properties 처리 중 오류", e)

            # artifact 5: appops
            if self.should_process_artifact("5"):
                try:
                    self.process_appops_zip()
                except Exception as e:
                    self.log_error("appops 처리 중 오류", e)

            # artifact 6: wellbing
            if self.should_process_artifact("6"):
                try:
                    self.process_wellbing_zip()
                except Exception as e:
                    self.log_error("wellbing 처리 중 오류", e)

            # artifact 7: internal
            if self.should_process_artifact("7"):
                try:
                    self.process_internal_zip(user_id)
                except Exception as e:
                    self.log_error("internal 처리 중 오류", e)

            # artifact 8: eRR.p
            if self.should_process_artifact("8"):
                try:
                    self.process_err_zip()
                except Exception as e:
                    self.log_error("eRR.p 처리 중 오류", e)
            
            # artifact 9: ULR_PERSISTENT_PREFS.xml
            if self.should_process_artifact("9"):
                try:
                    self.process_ulr_zip(user_id)
                except Exception as e:
                    self.log_error("ULR_PERSISTENT_PREFS.xml 처리 중 오류", e)
        except Exception as e:
            self.log_error("ZIP 아티팩트 처리 중 치명적 오류", e)
            raise

    def process_artifacts_folder(self, user_id):
        """폴더 모드에서 아티팩트 처리 (ZIP과 동일한 로직)"""
        # artifact 1: bootstat
        if self.should_process_artifact("1"):
            self.process_bootstat()

        # artifact 2-1: recovery.log
        if self.should_process_artifact("21") or self.should_process_artifact("2-1"):
            self.process_recovery_log_folder()

        # artifact 2-2: last_log
        if self.should_process_artifact("22") or self.should_process_artifact("2-2"):
            self.process_last_log_folder()

        # artifact 3: suggestions.xml
        if self.should_process_artifact("3"):
            self.process_suggestions_folder(user_id)

        # artifact 4: persistent_properties
        if self.should_process_artifact("4"):
            self.process_persistent_properties_folder()

        # artifact 5: appops
        if self.should_process_artifact("5"):
            self.process_appops_folder()

        # artifact 6: wellbing
        if self.should_process_artifact("6"):
            self.process_wellbing_folder()

        # artifact 7: internal
        if self.should_process_artifact("7"):
            self.process_internal_folder(user_id)

        # artifact 8: eRR.p
        if self.should_process_artifact("8"):
            self.process_err_folder()
        
        # artifact 9: ULR_PERSISTENT_PREFS.xml
        if self.should_process_artifact("9"):
            self.process_ulr_folder(user_id)

    def process_artifacts_adb(self, user_id):
        """ADB 모드에서 아티팩트 처리"""
        if self.should_process_artifact("1"):
            self.process_bootstat()
        # artifact 2-1: recovery.log
        if self.should_process_artifact("21") or self.should_process_artifact("2-1"):
            self.process_recovery_log_adb()

        # artifact 2-2: last_log
        if self.should_process_artifact("22") or self.should_process_artifact("2-2"):
            self.process_last_log_adb()

        # artifact 3: suggestions.xml
        if self.should_process_artifact("3"):
            self.process_suggestions_adb(user_id)

        # artifact 4: persistent_properties
        if self.should_process_artifact("4"):
            self.process_persistent_properties_adb()

        # artifact 5: appops
        if self.should_process_artifact("5"):
            self.process_appops_adb()

        # artifact 6: wellbing
        if self.should_process_artifact("6"):
            self.process_wellbing_adb()

        # artifact 7: internal
        if self.should_process_artifact("7"):
            self.process_internal_adb(user_id)
 
        # artifact 8: eRR.p
        if self.should_process_artifact("8"):
            self.process_err_adb()

    def _parse_get_system_time_from_line(self, line):
        """한 줄에서 get_system_time (상대시간, datetime) 추출"""
        rel = None
        rel_match = re.match(r'\[\s*(\d+\.\d+)\]', line)
        if rel_match:
            rel = float(rel_match.group(1))

        m = re.search(r'get_system_time=(\d{4}-\d{2}-\d{2})-(\d{2}:\d{2}:\d{2})', line)
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
                return rel, dt
            except ValueError:
                pass

        m = re.search(r'get_system_time=(\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2})', line)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d-%H:%M:%S")
                return rel, dt
            except ValueError:
                pass
        return None, None

    def _find_recovery_timeline_base(self, content):
        """get_system_time 또는 Starting recovery에서 타임라인 기준점 탐색"""
        lines = content.splitlines()
        gst_without_rel = None

        for i, line in enumerate(lines):
            rel, dt = self._parse_get_system_time_from_line(line)
            if not dt:
                continue
            if rel is not None:
                self.log(f"[기준 시간 발견] 라인 {i+1}: get_system_time={dt} (상대시간: {rel}초)")
                return dt, rel, 'get_system_time'
            if gst_without_rel is None:
                gst_without_rel = (dt, i + 1)

        if gst_without_rel:
            dt, line_no = gst_without_rel
            self.log(f"[기준 시간 발견] 라인 {line_no}: get_system_time={dt} (상대시간 없음, 0초 기준)")
            return dt, 0.0, 'get_system_time'

        starting_pattern = re.compile(
            r'Starting recovery.*?\bon\b\s+([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})',
            re.IGNORECASE
        )
        m = starting_pattern.search(content)
        if m:
            try:
                dt = datetime.strptime(" ".join(m.group(1).split()), "%a %b %d %H:%M:%S %Y")
                self.log(f"[기준 시간 fallback] Starting recovery: {dt} (상대시간=0, 오래된 recovery.log 대응)")
                return dt, 0.0, 'starting_recovery'
            except ValueError:
                pass

        return None, None, None

    def _report_recovery_wiping_data_status(self, content, file_path, artifact_id, wiping_timed, starting_dt=None):
        """-- Wiping data 로그 존재 여부를 표에 반영"""
        if not self.gui_instance:
            return

        wiping_pattern = re.compile(r'--\s*Wiping\s+data', re.IGNORECASE)
        wipe_lines = []
        for i, line in enumerate(content.splitlines()):
            if wiping_pattern.search(line):
                wipe_lines.append((i + 1, line.strip()))

        label_prefix = "recovery.log" if artifact_id == "21" else "last_log"

        if wipe_lines:
            if not wiping_timed:
                for line_no, msg in wipe_lines:
                    self.gui_instance.add_artifact_data(
                        artifact_id,
                        f"{label_prefix} (-- Wiping data, 시간 미계산)",
                        file_path,
                        starting_dt,
                        f"라인 {line_no}: {msg}\n"
                        "(get_system_time/Starting recovery 기준 없음으로 절대시각 미확정)",
                    )
            else:
                self.log(f"[{label_prefix}] '-- Wiping data' 로그 {len(wipe_lines)}건 확인 (시간 계산 완료)")
        else:
            message = (
                "'-- Wiping data' 로그가 없습니다. "
                "오래된 기기·로그 순환 후에는 해당 항목이 삭제되었을 수 있습니다."
            )
            if starting_dt:
                message += "\nStarting recovery 시각을 참고하세요."
            self.gui_instance.add_artifact_data(
                artifact_id,
                f"{label_prefix} (-- Wiping data 없음)",
                file_path,
                starting_dt,
                message,
            )

    def _parse_recovery_timeline(self, content, file_path, artifact_id):
        """recovery.log/last_log에서 기준 시간과 초기화 관련 로그 시간 계산"""
        empty_result = {'any_events': False, 'wiping_timed': False}
        if not content:
            return empty_result

        base_time, base_rel, base_source = self._find_recovery_timeline_base(content)
        if base_time is None:
            return empty_result

        lines = content.splitlines()
        wipe_keywords = [
            (re.compile(r'--\s*Wiping\s+data', re.IGNORECASE), "초기화 시작"),
            (re.compile(r'Wiping\s+/data', re.IGNORECASE), "초기화 시작 (/data)"),
            (re.compile(r'Data\s+wipe\s+complete', re.IGNORECASE), "초기화 완료"),
            (re.compile(r'Formatting\s+/data', re.IGNORECASE), "데이터 포맷팅 시작"),
            (re.compile(r'Info:\s*format\s+successful', re.IGNORECASE), "포맷 완료"),
        ]

        found_events = []
        for i, line in enumerate(lines):
            rel_match = re.match(r'\[\s*(\d+\.\d+)\]\s+(.*)$', line)
            if not rel_match:
                continue

            rel_time = float(rel_match.group(1))
            msg = rel_match.group(2)

            for pattern, event_name in wipe_keywords:
                if pattern.search(msg):
                    abs_time = base_time + timedelta(seconds=(rel_time - base_rel))
                    found_events.append({
                        'line': i + 1,
                        'rel_time': rel_time,
                        'abs_time': abs_time,
                        'event': event_name,
                        'message': msg.strip(),
                    })
                    self.log(
                        f"[초기화 이벤트] 라인 {i+1} (기준:{base_source}, 상대: {rel_time:.6f}초): "
                        f"{event_name} = {abs_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
                    )
                    break

        if found_events and self.gui_instance:
            import calendar
            label_prefix = "recovery.log" if artifact_id == "21" else "last_log"
            for event in found_events:
                utc_timestamp = calendar.timegm(event['abs_time'].utctimetuple())
                self.timestamp_process(
                    utc_timestamp,
                    artifact_id=artifact_id,
                    path=file_path,
                    name=f"{label_prefix} ({event['event']})",
                    original_time=f"라인 {event['line']}: {event['message']}",
                    is_kst=False,
                )

        wiping_timed = any(
            re.search(r'--\s*Wiping\s+data', e.get('message', ''), re.IGNORECASE)
            for e in found_events
        )
        return {
            'any_events': len(found_events) > 0,
            'wiping_timed': wiping_timed,
        }
    
    def _parse_recovery_reset_trigger(self, content, file_path, artifact_id):
        """recovery 로그 reason/requested_time/caller 파싱 — 원격·로컬 초기화 식별 (논문 §4.4)"""
        if not content or not self.gui_instance:
            return

        reason_text = ''
        requested_time = ''
        m_reason = re.search(r'reason\s+is\s*\[([^\]]*)\]', content, re.IGNORECASE)
        if m_reason:
            reason_text = m_reason.group(1).strip()
        m_req = re.search(r'--requested_time=([^\s"\'\\]+)', content)
        if m_req:
            requested_time = m_req.group(1).strip()

        reason_lower = reason_text.lower()
        if 'find my device' in reason_lower or 'remotely' in reason_lower or 'com.google.android.gms' in reason_lower:
            trigger_type = '원격 초기화 (Find My Device / GMS)'
        elif 'com.android.settings' in reason_lower or 'masterclearconfirm' in reason_lower or 'mainclearconfirm' in reason_lower:
            trigger_type = '로컬 초기화 (설정 앱)'
        elif reason_text in ('', '[]'):
            trigger_type = '로컬/미상 (reason 빈 값)'
        else:
            trigger_type = f'미분류 ({reason_text[:60]})'

        confirm_dt = None
        ts_match = re.search(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', reason_text)
        if ts_match:
            try:
                confirm_dt = datetime.strptime(ts_match.group(1).replace('T', ' '), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                confirm_dt = None

        detail_parts = []
        if reason_text:
            detail_parts.append(f"reason: {reason_text}")
        if requested_time:
            detail_parts.append(f"requested_time: {requested_time}")
        detail = '\n'.join(detail_parts) if detail_parts else 'reason/requested_time 없음'
        if requested_time:
            detail += "\n(논문: reason의 Z는 UTC 표지가 아닌 로컬시각 표기)"

        label = "recovery.log (초기화 트리거)" if artifact_id == "21" else "last_log (초기화 트리거)"
        self.gui_instance.add_artifact_data(
            artifact_id,
            label,
            file_path,
            confirm_dt,
            trigger_type,
            is_kst=True,
            original_time=detail,
        )
        self.log(f"[초기화 트리거] {trigger_type}")

    def _parse_recovery_log_content(self, content, file_path):
        """recovery.log 내용 파싱 (공통 로직) - UTC 0 기준"""
        if not content:
            return False
        
        success = False
        starting_dt = None
        
        # 1. Starting recovery 패턴 (기본)
        pattern = r'Starting recovery.*?\bon\b\s+([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})'
        matches = re.findall(pattern, content, flags=re.IGNORECASE)
        if matches:
            time_str = matches[0]
            try:
                # recovery.log는 UTC 0 기준이므로 naive datetime을 UTC로 간주
                dt_naive = datetime.strptime(" ".join(time_str.split()), "%a %b %d %H:%M:%S %Y")
                starting_dt = dt_naive
                # UTC 기준으로 epoch 계산: calendar.timegm() 사용 (UTC 기준)
                import calendar
                utc_timestamp = calendar.timegm(dt_naive.utctimetuple())
                
                self.log("******************************************")
                self.log(f"[2-1] [PATH : {file_path}]")
                self.log(f"recovery.log UTC 시간: {dt_naive} (UTC 0 기준, epoch: {utc_timestamp})")
                self.timestamp_process(utc_timestamp, artifact_id="21", path=file_path, name="recovery.log", original_time=time_str)
                self.log("******************************************\n")
                success = True
            except ValueError as e:
                self.log(f"[2-1] 날짜 파싱 오류: {e}")
        else:
            self.log_parse_failure(file_path, "recovery.log Starting recovery 패턴 불일치", content)
        
        # 2. 타임라인 분석 (-- Wiping data 등, get_system_time/Starting recovery 기준)
        timeline_result = self._parse_recovery_timeline(content, file_path, "21")
        if timeline_result.get('any_events'):
            success = True

        # 3. -- Wiping data 존재 여부 표시 (없으면 오래된 로그 안내)
        self._report_recovery_wiping_data_status(
            content, file_path, "21", timeline_result.get('wiping_timed'), starting_dt
        )

        # 4. 원격/로컬 초기화 트리거 식별 (reason, requested_time)
        self._parse_recovery_reset_trigger(content, file_path, "21")
        
        return success
    
    def _read_file_by_mode(self, file_path):
        """모드에 따라 파일 읽기"""
        if self.choice == "1":  # ZIP
            if self.search_zip(file_path):
                return self.read_file(file_path)
        elif self.choice == "2":  # ADB
            if self.adb_file_exists(file_path):
                return self.adb_read_file(file_path)
        elif self.choice == "3":  # Folder
            if self.search_zip(file_path):  # folder도 search_zip 사용
                return self.read_file(file_path)
        return None
    
    def _read_file_bytes_by_mode(self, file_path):
        """모드에 따라 바이너리 파일 읽기"""
        if self.choice == "1":  # ZIP
            if self.search_zip(file_path):
                return self.read_file_bytes(file_path)
        elif self.choice == "2":  # ADB
            if self.adb_file_exists(file_path):
                return self.adb_read_file_bytes(file_path)
        elif self.choice == "3":  # Folder
            if self.search_zip(file_path):
                return self.read_file_bytes(file_path)
        return None
    
    def _file_exists_by_mode(self, file_path):
        """모드에 따라 파일 존재 확인"""
        if self.choice == "1":  # ZIP
            return self.search_zip(file_path)
        elif self.choice == "2":  # ADB
            return self.adb_file_exists(file_path)
        elif self.choice == "3":  # Folder
            return self.search_zip(file_path)  # folder도 search_zip 사용
        return False
    
    def process_recovery_log(self):
        """recovery.log 처리 (모든 모드 공통)"""
        recovery_success = False
        found_path = None
        
        # 모드에 따라 경로 설정
        if self.choice == "2":  # ADB
                targets = [
                "/data/log/Recovery.log",
                "/data/log/recovery.log",
                "/cache/recovery/log",
            ]
        else:  # ZIP or Folder
            targets = [
                "Dump/data/log/Recovery.log",
                "Dump/data/log/recovery.log",
                "Dump/cache/recovery/log",
            ]
        
        for target_file in targets:
            if self._file_exists_by_mode(target_file):
                found_path = target_file
                try:
                    content = self._read_file_by_mode(target_file)
                    if self._parse_recovery_log_content(content, target_file):
                        recovery_success = True
                        break
                except Exception as e:
                    self.log(f"[2-1] recovery.log 처리 중 오류: {e}")
        
        if not recovery_success:
            self.log("******************************************")
            self.log("[2-1] [recovery.log 파일이 존재하지 않거나 시간 정보가 없습니다.]")
            self.log("******************************************\n")
            # 시간이 없어도 표에 추가
            if self.gui_instance:
                self.gui_instance.add_artifact_data(
                    "21",
                    "recovery.log",
                    found_path or "",
                    None,
                    "파일이 존재하지 않거나 시간 정보가 없습니다."
                )
    
    def process_recovery_log_zip(self):
        """ZIP 모드용 (하위 호환성)"""
        self.process_recovery_log()
    
    def process_recovery_log_folder(self):
        """Folder 모드용 (하위 호환성)"""
        self.process_recovery_log()
    
    def process_recovery_log_adb(self):
        """ADB 모드용 (하위 호환성)"""
        self.process_recovery_log()
    
    def _parse_last_log_content(self, content, raw_bytes, file_path):
        """last_log 내용 파싱 (공통 로직) - UTC 0 기준"""
        if not content:
            return False

        success = False
        parse_recalc_enabled = False

        # 1. Starting recovery 패턴 (기본) - recovery.log와 동일 로직
        pattern = r'Starting recovery.*?\bon\b\s+([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})'
        matches = re.findall(pattern, content, flags=re.IGNORECASE)
        if matches:
            time_str = matches[0]
            try:
                # last_log도 UTC 0 기준이므로 naive datetime을 UTC로 간주
                dt_naive = datetime.strptime(time_str, "%a %b %d %H:%M:%S %Y")
                # UTC 기준으로 epoch 계산: calendar.timegm() 사용 (UTC 기준)
                import calendar
                utc_timestamp = calendar.timegm(dt_naive.utctimetuple())

                self.log("******************************************")
                self.log(f"[2-2] [PATH : {file_path}]")
                self.log(f"last_log UTC 시간: {dt_naive} (UTC 0 기준, epoch: {utc_timestamp})")
                self.timestamp_process(utc_timestamp, artifact_id="22", path=file_path, name="last_log", original_time=time_str)
                self.log("******************************************\n")
                success = True
            except ValueError as e:
                self.log(f"[2-2] 날짜 파싱 오류: {e}")
        else:
            self.log_parse_failure(file_path, "last_log 패턴 불일치", content)

        starting_dt = None
        if matches:
            try:
                starting_dt = datetime.strptime(" ".join(matches[0].split()), "%a %b %d %H:%M:%S %Y")
            except ValueError:
                starting_dt = None

        # 2. 타임라인 분석 (초기화 시간 계산) - recovery.log와 동일 흐름
        timeline_result = self._parse_recovery_timeline(content, file_path, "22")
        if timeline_result.get('any_events'):
            success = True

        self._report_recovery_wiping_data_status(
            content, file_path, "22", timeline_result.get('wiping_timed'), starting_dt
        )

        self._parse_recovery_reset_trigger(content, file_path, "22")
        
        # Xiaomi 타임라인 시도 (기존 방식과 병행 추가: 둘 다 결과가 나올 수 있음)
        if parse_recalc_enabled and raw_bytes:
            text = raw_bytes.decode("utf-8", errors="ignore")
            parsed = self.parse_xiaomi_last_log_timeline(text)
            if parsed:
                self.log("******************************************")
                self.log(f"[2-2] [Xiaomi last_log 타임라인] [PATH : {file_path}]")
                self.log(f"BASE get_system_time: {parsed['base_dt'].strftime('%Y-%m-%d %H:%M:%S')} UTC (rel={parsed['base_rel']:.6f}s)")
                
                # recovery.log와 동일하게 base 시간도 UTC 기준으로 저장
                if self.gui_instance:
                    import calendar
                    utc_timestamp = calendar.timegm(parsed['base_dt'].utctimetuple())
                    self.timestamp_process(
                        utc_timestamp,
                        artifact_id="22",
                        path=file_path,
                        name="last_log (재계산 base)",
                        original_time=f"get_system_time={parsed['base_dt'].strftime('%Y-%m-%d %H:%M:%S')}",
                        is_kst=False
                    )
                
                # 타임라인 이벤트들도 추가 (초기화 관련만)
                if self.gui_instance:
                    import calendar
                    for abs_str, rel, msg in parsed["timeline"]:
                        # abs_str에서 시간 추출 (UTC 기준)
                        try:
                            abs_dt_str = abs_str.replace(" KST", "").replace(" UTC", "").strip()
                            abs_dt = datetime.strptime(abs_dt_str, "%Y-%m-%d %H:%M:%S.%f")
                            utc_timestamp = calendar.timegm(abs_dt.utctimetuple())
                            
                            # 초기화 관련 이벤트만 추가
                            if any(k in msg for k in ["-- Wiping data", "Data wipe complete", "Formatting /data", "Info: format successful"]):
                                event_name = "초기화 시작" if "Wiping" in msg else "초기화 완료" if "complete" in msg or "complete" in msg.lower() else "포맷팅"
                                self.timestamp_process(
                                    utc_timestamp,
                                    artifact_id="22",
                                    path=file_path,
                                    name=f"last_log (재계산 {event_name})",
                                    original_time=f"라인: {msg}",
                                    is_kst=False
                                )
                        except Exception as e:
                            self.log(f"[Xiaomi 타임라인 파싱 오류] {abs_str}: {e}")
                        
                        self.log(f"{abs_str}  (rel={rel:9.6f}s)  {msg}")
                self.log("******************************************\n")
                success = True
        
        return success
    
    def process_last_log(self):
        """last_log 처리 (모든 모드 공통)"""
        last_log_success = False
        found_path = None
        
        # 모드에 따라 경로 설정
        if self.choice == "2":  # ADB
            targets = ["/cache/recovery/last_log"]
        else:  # ZIP or Folder
                targets = [
                "Dump/cache/recovery/last_log",
                    "Dump/mnt/rescue/recovery/last_log",
                    "Dump/mnt/rescue/recovery/last_log.1",
                    "Dump/mnt/rescue/recovery/last_kmsg",
                    "Dump/mnt/rescue/recovery/last_kmsg.1",
                ]
        
        for target_file in targets:
            if self._file_exists_by_mode(target_file):
                found_path = target_file
                try:
                    content = self._read_file_by_mode(target_file)
                    raw_bytes = self._read_file_bytes_by_mode(target_file)
                    if self._parse_last_log_content(content, raw_bytes, target_file):
                        last_log_success = True
                        break
                except Exception as e:
                    self.log(f"[2-2] last_log 처리 중 오류: {e}")
        
        if not last_log_success:
            self.log("******************************************")
            self.log("[2-2] [last_log 파일이 존재하지 않거나 시간 정보가 없습니다.]")
            self.log("******************************************\n")
            if self.gui_instance:
                self.gui_instance.add_artifact_data(
                    "22",
                    "last_log",
                    found_path or "",
                    None,
                    "파일이 존재하지 않거나 시간 정보가 없습니다."
                )
    
    def process_last_log_zip(self):
        """ZIP 모드용 (하위 호환성)"""
        self.process_last_log()
    
    def process_last_log_folder(self):
        """Folder 모드용 (하위 호환성)"""
        self.process_last_log()
    
    def process_last_log_adb(self):
        """ADB 모드용 (하위 호환성)"""
        self.process_last_log()
    
    def _parse_suggestions_content(self, content, file_path):
        """suggestions.xml / setup_wizard_info.xml 내용 파싱 (공통 로직)"""
        if not content:
            return False
        
        display_name = os.path.basename(file_path) if file_path else "suggestions.xml"
        patterns = [
            r'<long name="com\.android\.settings\.suggested\.category\.DEFERRED_SETUP_setup_time"\s+value="(\d+)"',
            r'<long name="DEFERRED_SETUP_setup_time"\s+value="(\d+)"',
            r'name="setup_time"\s+value="(\d+)"',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content)
            if matches:
                self.log("******************************************")
                self.log(f"[3] [PATH : {file_path}]")
                self.timestamp_process(matches[0], artifact_id="3", path=file_path, name=display_name)
                self.log("******************************************\n")
                return True
        self.log_parse_failure(file_path, f"{display_name} 값 없음", content)
        return False
    
    def process_suggestions(self, user_id):
        """suggestions.xml 처리 (모든 모드 공통)"""
        suggestion_success = False
        found_path = None
        pattern = r'<long name="com\.android\.settings\.suggested\.category\.DEFERRED_SETUP_setup_time"\s+value="(\d+)"'
        
        # 모드에 따라 경로 설정
        if self.choice == "2":  # ADB
            targets = [
                "/data/data/com.android.settings.intelligence/shared_prefs/suggestions.xml",
                "/data/data/com.android.settings.intelligence/shared_prefs/setup_wizard_info.xml",
                f"/data/user/{user_id}/com.google.android.settings.intelligence/shared_prefs/suggestions.xml",
                f"/data/user/{user_id}/com.google.android.settings.intelligence/shared_prefs/setup_wizard_info.xml",
                f"/data/user_de/{user_id}/com.google.android.settings.intelligence/shared_prefs/setup_wizard_info.xml",
                f"/data/user_de/{user_id}/com.google.android.settings.intelligence/shared_prefs/suggestions.xml",
            ]
        else:  # ZIP or Folder
            targets = [
                "Dump/data/data/com.android.settings.intelligence/shared_prefs/suggestions.xml",
                "Dump/data/data/com.android.settings.intelligence/shared_prefs/setup_wizard_info.xml",
                f"Dump/data/user/{user_id}/com.google.android.settings.intelligence/shared_prefs/suggestions.xml",
                f"Dump/data/user/{user_id}/com.google.android.settings.intelligence/shared_prefs/setup_wizard_info.xml",
                f"Dump/data/user_de/{user_id}/com.google.android.settings.intelligence/shared_prefs/suggestions.xml",
                f"Dump/data/user_de/{user_id}/com.google.android.settings.intelligence/shared_prefs/setup_wizard_info.xml",
            ]
        
        for target_file in targets:
            if self._file_exists_by_mode(target_file):
                found_path = target_file
                try:
                    if self.choice in ["1", "3"]:  # ZIP or Folder
                        extracted, matches = self.search_timestamp_in_property(target_file, pattern)
                        if extracted is not None and matches:
                            self.log("******************************************")
                            self.log(f"[3] [PATH : {target_file}]")
                            self.timestamp_process(matches[0], artifact_id="3", path=target_file, name="suggestions.xml")
                            self.log("******************************************\n")
                            suggestion_success = True
                            break
                    else:  # ADB
                        content = self._read_file_by_mode(target_file)
                        if self._parse_suggestions_content(content, target_file):
                            suggestion_success = True
                            break
                except Exception as e:
                    self.log(f"[3] suggestions.xml 처리 중 오류: {e}")
        
        if not suggestion_success:
            self.log("******************************************")
            self.log("[3] [suggestions.xml 파일이 존재하지 않거나 값이 없습니다.]")
            self.log("******************************************\n")
            if self.gui_instance:
                self.gui_instance.add_artifact_data(
                    "3",
                    "suggestions.xml",
                    found_path or "",
                    None,
                    "파일이 존재하지 않거나 값이 없습니다."
                )
    
    def process_suggestions_zip(self, user_id):
        """ZIP 모드용 (하위 호환성)"""
        self.process_suggestions(user_id)
    
    def process_suggestions_folder(self, user_id):
        """Folder 모드용 (하위 호환성)"""
        self.process_suggestions(user_id)
    
    def process_suggestions_adb(self, user_id):
        """ADB 모드용 (하위 호환성)"""
        self.process_suggestions(user_id)
    
    def _parse_persistent_properties_content(self, content, file_path):
        """persistent_properties 내용 파싱 (공통 로직)"""
        if not content:
            return False
        
        keyword = "reboot,factory_reset"
        # 패턴: reboot,factory_reset 뒤에 쉼표나 공백/콜론/등호가 오고 10자리 이상 숫자
        # 예: persist.sys.boot.reason.history.reboot,factory_reset,1689128778
        # 쉼표로 바로 연결된 경우도 처리: reboot,factory_reset,1689128778
        # 개행 문자도 고려하여 여러 패턴 시도
        
        # 패턴 1: 쉼표 바로 뒤에 숫자 (가장 일반적)
        pattern1 = rf"{re.escape(keyword)},(\d{{10,}})"
        matches = re.findall(pattern1, content)
        
        # 패턴 2: 공백/개행 후 숫자
        if not matches:
            pattern2 = rf"{re.escape(keyword)}[\s,:=]+(\d{{10,}})"
            matches = re.findall(pattern2, content, re.MULTILINE)
        
        # 패턴 3: 더 유연한 패턴 (개행 포함)
        if not matches:
            pattern3 = rf"{re.escape(keyword)}[,\s:=]+(\d{{10,}})"
            matches = re.findall(pattern3, content, re.DOTALL)
        
        if matches:
            # 전체 매칭 문자열 찾기 (원본 시간 저장용)
            full_pattern = rf"{re.escape(keyword)}[,\s:=]+(\d{{10,}})"
            full_match = re.search(full_pattern, content, re.MULTILINE | re.DOTALL)
            if full_match:
                original_time_str = full_match.group(0)
            else:
                original_time_str = f"{keyword},{matches[0]}"
            
            self.log("******************************************")
            self.log(f"[4] [PATH : {file_path}]")
            self.log(f"[4] [매칭된 값] {matches[0]}")
            self.timestamp_process(matches[0], artifact_id="4", path=file_path, name="persistent_properties", original_time=original_time_str)
            self.log("******************************************\n")
            return True
        else:
            # 디버깅: 내용 일부 출력
            content_preview = content[:500] if len(content) > 500 else content
            self.log("******************************************")
            self.log(f"[4] [PATH : {file_path}]")
            self.log("[4] [값이 존재하지 않습니다.]")
            self.log(f"[4] [디버깅] 내용 미리보기:\n{content_preview}")
            self.log("******************************************\n")
            self.log_parse_failure(file_path, "persistent_properties 값 없음", content)
            if self.gui_instance:
                self.gui_instance.add_artifact_data("4", "persistent_properties", file_path, None, "값이 존재하지 않습니다.")
        return False
    
    def process_persistent_properties(self):
        """persistent_properties 처리 (모든 모드 공통)"""
        if self.choice == "2":  # ADB
            target_file = "/data/property/persistent_properties"
        else:  # ZIP or Folder
                target_file = "Dump/data/property/persistent_properties"
        
        if self._file_exists_by_mode(target_file):
            try:
                if self.choice in ["1", "3"]:  # ZIP or Folder
                    keyword = "reboot,factory_reset"
                    # 패턴: reboot,factory_reset 뒤에 쉼표나 공백/콜론/등호가 오고 10자리 숫자
                    # 예: persist.sys.boot.reason.history.reboot,factory_reset,1689128778
                    # 쉼표로 바로 연결된 경우도 처리: reboot,factory_reset,1689128778
                    # 패턴 수정: 쉼표 바로 뒤에 숫자가 오는 경우도 처리, 10자리 이상 숫자 허용
                    pattern = rf"{re.escape(keyword)}[,\s:=]+(\d{{10,}})"
                    resulttime, matches = self.search_timestamp_in_property(target_file, pattern)
                    if resulttime is not None and matches:
                        # 전체 매칭 문자열 찾기 (원본 시간 저장용)
                        content = self._read_file_by_mode(target_file)
                        if content:
                            full_pattern = rf"{re.escape(keyword)}[,\s:=]+(\d{{10,}})"
                            full_match = re.search(full_pattern, content, re.MULTILINE | re.DOTALL)
                            original_time_str = full_match.group(0) if full_match else matches[0]
                        else:
                            original_time_str = matches[0]
                        
                        self.log("******************************************")
                        self.log(f"[4] [PATH : {target_file}]")
                        self.timestamp_process(matches[0], artifact_id="4", path=target_file, name="persistent_properties", original_time=original_time_str)
                        self.log("******************************************\n")
                    else:
                        content = self._read_file_by_mode(target_file)
                        self._parse_persistent_properties_content(content, target_file)
                else:  # ADB
                    content = self._read_file_by_mode(target_file)
                    self._parse_persistent_properties_content(content, target_file)
            except Exception as e:
                self.log(f"Persistent properties 처리 중 오류: {e}")
                import traceback
                self.log(traceback.format_exc())
        else:
            if self.choice == "2":
                self.log(f"{target_file} does not exist on device.")
            else:
                self.log(f"{target_file}이(가) ZIP 파일에 존재하지 않습니다.")
            if self.gui_instance:
                self.gui_instance.add_artifact_data("4", "persistent_properties", target_file, None, "파일이 존재하지 않습니다.")
    
    def process_persistent_properties_zip(self):
        """ZIP 모드용 (하위 호환성)"""
        self.process_persistent_properties()
    
    def process_persistent_properties_folder(self):
        """Folder 모드용 (하위 호환성)"""
        self.process_persistent_properties()
    
    def process_persistent_properties_adb(self):
        """ADB 모드용 (하위 호환성)"""
        self.process_persistent_properties()
    
    def process_appops(self):
        """appops.xml 처리 (모든 모드 공통)"""
        if self.choice == "2":  # ADB
            target_file = "/data/system/appops.xml"
        else:  # ZIP or Folder
                target_file = "Dump/data/system/appops.xml"
        
        self.log("******************************************")
        if self._file_exists_by_mode(target_file):
            matchtimeonly = self.extract_from_binary_xml(target_file, adb_mode=(self.choice == "2"))
            if matchtimeonly:
                self.log(f"[5] [PATH : {target_file}]")
                self.timestamp_process(matchtimeonly[0], artifact_id="5", path=target_file, name="appops.xml")
            else:
                self.log("[5] [no timestamp in appops.xml]")
                content = self._read_file_by_mode(target_file)
                self.log_parse_failure(target_file, "appops.xml 타임스탬프 없음", content)
                if self.gui_instance:
                    self.gui_instance.add_artifact_data("5", "appops.xml", target_file, None, "타임스탬프가 없습니다.")
        else:
            if self.choice == "2":
                self.log(f"{target_file} does not exist on device.")
            else:
                self.log(f"{target_file}이(가) ZIP 파일에 존재하지 않습니다.")
            if self.gui_instance:
                self.gui_instance.add_artifact_data("5", "appops.xml", target_file, None, "파일이 존재하지 않습니다.")
        self.log("******************************************\n")
    
    def process_appops_zip(self):
        """ZIP 모드용 (하위 호환성)"""
        self.process_appops()
    
    def process_appops_folder(self):
        """Folder 모드용 (하위 호환성)"""
        self.process_appops()
    
    def process_appops_adb(self):
        """ADB 모드용 (하위 호환성)"""
        self.process_appops()
    
    def process_wellbing_zip(self):
                queryforpixel = """
                    SELECT events._id,
                           datetime(events.timestamp/1000, 'UNIXEPOCH') as timestamps,
                           packages.package_name, events.type,
                           CASE
                               when events.type=1 THEN 'ACTIVITY_RESUMED'
                               when events.type=2 THEN 'ACTIVITY_PAUSED'
                               when events.type=12 THEN 'NOTIFICATION'
                               when events.type=18 THEN 'KEYGUARD_HIDDEN || DEVICE UNLOCK'
                               when events.type=19 THEN 'FOREGROUND_SERVICE START'
                               when events.type=20 THEN 'FOREGROUND_SERVICE_STOP'
                               when events.type=23 THEN 'ACTIVITY_STOPPED'
                               when events.type=26 THEN 'DEVICE_SHUTDOWN'
                               when events.type=27 THEN 'DEVICE_STARTUP'
                               else events.type
                           END as eventtype
                    FROM events
                    INNER JOIN packages ON events.package_id=packages._id
                    ORDER by timestamps
                """
                queryforgalaxy = """
                    SELECT usageEvents.eventId,
                           datetime(usageEvents.timeStamp/1000, 'UNIXEPOCH') as timestamp,
                           foundPackages.name, usageEvents.eventType,
                           CASE
                               when usageEvents.eventType=1 THEN 'ACTIVITY_RESUMED'
                               when usageEvents.eventType=2 THEN 'ACTIVITY_PAUSED'
                               when usageEvents.eventType=5 THEN 'CONFIGURATION_CHANGE'
                               when usageEvents.eventType=7 THEN 'USER_INTERACTION'
                               when usageEvents.eventType=10 THEN 'NOTIFICATION PANEL'
                               when usageEvents.eventType=11 THEN 'STANDBY_BUCKET_CHANGED'
                               when usageEvents.eventType=12 THEN 'NOTIFICATION'
                               when usageEvents.eventType=15 THEN 'SCREEN_INTERACTIVE (Screen on for full user interaction)'
                               when usageEvents.eventType=16 THEN 'SCREEN_NON_INTERACTIVE (Screen on in Non-interactive state or completely turned off)'
                               when usageEvents.eventType=17 THEN 'KEYGUARD_SHOWN || POSSIBLE DEVICE LOCK'
                               when usageEvents.eventType=18 THEN 'KEYGUARD_HIDDEN || DEVICE UNLOCK'
                               when usageEvents.eventType=19 THEN 'FOREGROUND_SERVICE START'
                               when usageEvents.eventType=20 THEN 'FOREGROUND_SERVICE_STOP'
                               when usageEvents.eventType=23 THEN 'ACTIVITY_STOPPED'
                               when usageEvents.eventType=26 THEN 'DEVICE_SHUTDOWN'
                               when usageEvents.eventType=27 THEN 'DEVICE_STARTUP'
                               else usageEvents.eventType
                           END as eventTypeDescription
                    FROM usageEvents
                    INNER JOIN foundPackages ON usageEvents.pkgId=foundPackages.pkgId
                    ORDER BY timestamp
                """
                wellbing_success = False
                for target_file in ["Dump/data/data/com.google.android.apps.wellbeing/databases/app_usage",
                                    "Dump/data/data/com.samsung.android.forest/databases/dwbCommon.db"]:
                    if self.search_zip(target_file):
                        dbresult = self.execute_wellbing_query(
                            target_file,
                            queryforpixel if "wellbeing" in target_file else queryforgalaxy
                        )
                        self.log("******************************************")
                        self.log(f"[6] [PATH : {target_file}]")
                        self.log(str(dbresult))
                        self.log("******************************************\n")
                        wellbing_success = True
                        if self.gui_instance and (dbresult is None or str(dbresult).strip() == "" or str(dbresult).strip() == "None"):
                            self.gui_instance.add_artifact_data("6", "wellbing", target_file, None, "시간 정보가 없습니다.")
                        break
                if not wellbing_success:
                    self.log("There is no wellbing file in phone")
                    # ????????????? ???
                    if self.gui_instance:
                        self.gui_instance.add_artifact_data("6", "wellbing", "", None, "파일이 존재하지 않습니다.")

    def process_wellbing_folder(self):
        self.process_wellbing_zip()  # 동일한 로직
    
    def process_wellbing_adb(self):
                queryforpixel = """
                    SELECT events._id,
                           datetime(events.timestamp/1000, 'UNIXEPOCH') as timestamps,
                           packages.package_name, events.type,
                           CASE
                               when events.type=1 THEN 'ACTIVITY_RESUMED'
                               when events.type=2 THEN 'ACTIVITY_PAUSED'
                               when events.type=12 THEN 'NOTIFICATION'
                               when events.type=18 THEN 'KEYGUARD_HIDDEN || DEVICE UNLOCK'
                               when events.type=19 THEN 'FOREGROUND_SERVICE START'
                               when events.type=20 THEN 'FOREGROUND_SERVICE_STOP'
                               when events.type=23 THEN 'ACTIVITY_STOPPED'
                               when events.type=26 THEN 'DEVICE_SHUTDOWN'
                               when events.type=27 THEN 'DEVICE_STARTUP'
                               else events.type
                           END as eventtype
                    FROM events
                    INNER JOIN packages ON events.package_id=packages._id
                    ORDER by timestamps
                """
                queryforgalaxy = """
                    SELECT usageEvents.eventId,
                           datetime(usageEvents.timeStamp/1000, 'UNIXEPOCH') as timestamp,
                           foundPackages.name, usageEvents.eventType,
                           CASE
                               when usageEvents.eventType=1 THEN 'ACTIVITY_RESUMED'
                               when usageEvents.eventType=2 THEN 'ACTIVITY_PAUSED'
                               when usageEvents.eventType=5 THEN 'CONFIGURATION_CHANGE'
                               when usageEvents.eventType=7 THEN 'USER_INTERACTION'
                               when usageEvents.eventType=10 THEN 'NOTIFICATION PANEL'
                               when usageEvents.eventType=11 THEN 'STANDBY_BUCKET_CHANGED'
                               when usageEvents.eventType=12 THEN 'NOTIFICATION'
                               when usageEvents.eventType=15 THEN 'SCREEN_INTERACTIVE (Screen on for full user interaction)'
                               when usageEvents.eventType=16 THEN 'SCREEN_NON_INTERACTIVE (Screen on in Non-interactive state or completely turned off)'
                               when usageEvents.eventType=17 THEN 'KEYGUARD_SHOWN || POSSIBLE DEVICE LOCK'
                               when usageEvents.eventType=18 THEN 'KEYGUARD_HIDDEN || DEVICE UNLOCK'
                               when usageEvents.eventType=19 THEN 'FOREGROUND_SERVICE START'
                               when usageEvents.eventType=20 THEN 'FOREGROUND_SERVICE_STOP'
                               when usageEvents.eventType=23 THEN 'ACTIVITY_STOPPED'
                               when usageEvents.eventType=26 THEN 'DEVICE_SHUTDOWN'
                               when usageEvents.eventType=27 THEN 'DEVICE_STARTUP'
                               else usageEvents.eventType
                           END as eventTypeDescription
                    FROM usageEvents
                    INNER JOIN foundPackages ON usageEvents.pkgId=foundPackages.pkgId
                    ORDER BY timestamp
                """
                wellbing_success = False
                for target_file in ["/data/data/com.google.android.apps.wellbeing/databases/app_usage",
                            "/data/data/com.samsung.android.forest/databases/dwbCommon.db"]:
                    if self.adb_file_exists(target_file):
                        local_temp = "temp_db.db"
                        if self.adb_pull_file(target_file, local_temp):
                            df = self.execute_wellbing_query_local(
                                local_temp,
                                queryforpixel if "wellbeing" in target_file else queryforgalaxy
                            )
                            self.log("******************************************")
                            self.log(f"[6] [PATH : {target_file}]")
                            self.log(str(df))
                            self.log("******************************************\n")
                            wellbing_success = True
                            if self.gui_instance and (df is None or str(df).strip() == "" or str(df).strip() == "None"):
                                self.gui_instance.add_artifact_data("6", "wellbing", target_file, None, "시간 정보가 없습니다.")
                            break
                if not wellbing_success:
                    self.log("There is no wellbing file in device.")
                    # ????????????? ???
                    if self.gui_instance:
                        self.gui_instance.add_artifact_data("6", "wellbing", "", None, "파일이 존재하지 않습니다.")

    def process_internal_zip(self, user_id):
                internal_success = False
                targets = [
            "Dump/data/data/com.android.providers.media/databases/internal.db",
            f"Dump/data/user/{user_id}/com.google.android.providers.media.module/database/internal.db",
                    "Dump/data/data/com.android.providers.media.module/databases/internal.db",
            "Dump/data/data/com.google.android.providers.media.module/databases/internal.db",
                    f"Dump/data/user/{user_id}/com.android.providers.media.module/databases/internal.db"
                ]
                self.log("[경로 후보] internal.db ZIP 검색 경로:")
                for t in targets:
                    self.log(f"  - {t}")
                for target_file in targets:
                    if self.search_zip(target_file):
                        dbresult = self.execute_wellbing_query(target_file, None)
                        self.log("******************************************")
                        self.log(f"[7] [PATH : {target_file}]")
                        if dbresult:
                            self.timestamp_process(dbresult, artifact_id="7", path=target_file, name="internal.db")
                            self.log("******************************************\n")
                            internal_success = True
                            break
                if not internal_success:
                    self.log("Internal DB file not found in ZIP.")
                    # ????????????? ???
                    if self.gui_instance:
                        self.gui_instance.add_artifact_data("7", "internal.db", "", None, "Internal DB ???????? ????????.")

    def process_internal_folder(self, user_id):
        self.process_internal_zip(user_id)  # 동일한 로직
    
    def process_internal_adb(self, user_id):
        internal_success = False
        targets = [
            f"/data/data/com.android.providers.media/databases/internal.db",
            f"/data/user/{user_id}/com.google.android.providers.media.module/databases/internal.db",
            "/data/data/com.android.providers.media.module/databases/internal.db",
            f"/data/user/{user_id}/com.android.providers.media.module/databases/internal.db"
        ]
        self.log("[경로 후보] internal.db ADB 검색 경로:")
        for t in targets:
            self.log(f"  - {t}")
        for target_file in targets:
            if self.adb_file_exists(target_file):
                local_temp = "temp_db.db"
                if self.adb_pull_file(target_file, local_temp):
                    result = self.execute_internal_query_local(local_temp)
                    self.log("******************************************")
                    self.log(f"[7] [PATH : {target_file}]")
                    if result:
                        self.timestamp_process(result, artifact_id="7", path=target_file, name="internal.db")
                    else:
                        # No timestamp found
                        if self.gui_instance:
                            self.gui_instance.add_artifact_data("7", "internal.db", target_file, None, "No timestamp found.")
                    self.log("******************************************\n")
                    internal_success = True
                    break
        if not internal_success:
            self.log("Internal DB file not found on device.")
            # Internal DB not found
            if self.gui_instance:
                self.gui_instance.add_artifact_data("7", "internal.db", "", None, "Internal DB file not found.")

    def _parse_err_content(self, content, file_path):
        """eRR.p 내용 파싱 (공통 로직)"""
        parsed = self.parse_err_rst_stat(content)
        if parsed and self.gui_instance:
            for dt_str, dt_obj in parsed:
                self.gui_instance.add_artifact_data(
                    "8",
                    "eRR.p (RST_STAT)",
                    file_path,
                    dt_obj,
                    None,
                    is_kst=True,
                    original_time=dt_str
                )
        return parsed
    
    def process_err(self):
        """eRR.p 처리 (모든 모드 공통)"""
        if self.choice == "2":  # ADB
            target_file = '/data/system/users/service/data/eRR.p'
        else:  # ZIP or Folder
                target_file = 'Dump/data/system/users/service/data/eRR.p'
        
        if self._file_exists_by_mode(target_file):
            result = self._read_file_by_mode(target_file)
            parsed = self._parse_err_content(result, target_file)
            if not parsed and self.gui_instance:
                self.gui_instance.add_artifact_data("8", "eRR.p", target_file, None, str(result) if result else "파일 내용 없음")
        else:
            result = "eRR.p 파일이 존재하지 않습니다."
            if self.gui_instance:
                self.gui_instance.add_artifact_data("8", "eRR.p", target_file, None, "파일이 존재하지 않습니다.")
        
        self.log("******************************************")
        self.log(f"[8] [PATH : {target_file}]")
        self.log(str(result))
        self.log("******************************************\n")
    
    def process_err_zip(self):
        """ZIP 모드용 (하위 호환성)"""
        self.process_err()
    
    def process_err_folder(self):
        """Folder 모드용 (하위 호환성)"""
        self.process_err()
    
    def process_err_adb(self):
        """ADB 모드용 (하위 호환성)"""
        self.process_err()
    
    def _parse_ulr_content(self, content, file_path):
        """ULR_PERSISTENT_PREFS.xml 내용 파싱 (공통 로직)"""
        if not content:
            return False
        
        pattern = r'<long name="reportingAutoenableManagerInitTimeMillisKey"\s+value="(\d+)"'
        matches = re.findall(pattern, content)
        if matches:
            self.log("******************************************")
            self.log(f"[9] [PATH : {file_path}]")
            # millisecond를 second로 변환
            timestamp_ms = int(matches[0])
            timestamp_s = timestamp_ms / 1000.0
            self.timestamp_process(
                timestamp_s,
                artifact_id="9",
                path=file_path,
                name="ULR_PERSISTENT_PREFS.xml",
                original_time=str(timestamp_ms),
            )
            self.log("******************************************\n")
            return True
        else:
            self.log_parse_failure(file_path, "ULR_PERSISTENT_PREFS.xml 값 없음", content)
        return False
    
    def process_ulr(self, user_id):
        """ULR_PERSISTENT_PREFS.xml 처리 (모든 모드 공통)"""
        ulr_success = False
        found_path = None
        pattern = r'<long name="reportingAutoenableManagerInitTimeMillisKey"\s+value="(\d+)"'
        
        # 모드에 따라 경로 설정
        if self.choice == "2":  # ADB
            targets = [
                f"/data/data/com.google.android.gms/shared_prefs/ULR_PERSISTENT_PREFS.xml",
                f"/data/user/{user_id}/com.google.android.gms/shared_prefs/ULR_PERSISTENT_PREFS.xml"
            ]
        else:  # ZIP or Folder
            targets = [
                "Dump/data/data/com.google.android.gms/shared_prefs/ULR_PERSISTENT_PREFS.xml",
                f"Dump/data/user/{user_id}/com.google.android.gms/shared_prefs/ULR_PERSISTENT_PREFS.xml"
            ]
        
        for target_file in targets:
            if self._file_exists_by_mode(target_file):
                found_path = target_file
                try:
                    if self.choice in ["1", "3"]:  # ZIP or Folder
                        extracted, matches = self.search_timestamp_in_property(target_file, pattern)
                        if extracted is not None and matches:
                            self.log("******************************************")
                            self.log(f"[9] [PATH : {target_file}]")
                            # millisecond를 second로 변환
                            timestamp_ms = int(matches[0])
                            timestamp_s = timestamp_ms / 1000.0
                            self.timestamp_process(
                                timestamp_s,
                                artifact_id="9",
                                path=target_file,
                                name="ULR_PERSISTENT_PREFS.xml",
                                original_time=str(timestamp_ms),
                            )
                            self.log("******************************************\n")
                            ulr_success = True
                            break
                        else:
                            content = self._read_file_by_mode(target_file)
                            self.log_parse_failure(target_file, "ULR_PERSISTENT_PREFS.xml 값 없음", content)
                    else:  # ADB
                        content = self._read_file_by_mode(target_file)
                        if self._parse_ulr_content(content, target_file):
                            ulr_success = True
                            break
                except Exception as e:
                    self.log(f"[9] ULR_PERSISTENT_PREFS.xml 처리 중 오류: {e}")
        
        if not ulr_success:
            self.log("******************************************")
            self.log("[9] [ULR_PERSISTENT_PREFS.xml 파일이 존재하지 않거나 값이 없습니다.]")
            self.log("******************************************\n")
            if self.gui_instance:
                self.gui_instance.add_artifact_data(
                    "9",
                    "ULR_PERSISTENT_PREFS.xml",
                    found_path or "",
                    None,
                    "파일이 존재하지 않거나 값이 없습니다."
                )
    
    def process_ulr_zip(self, user_id):
        """ZIP 모드용 (하위 호환성)"""
        self.process_ulr(user_id)
    
    def process_ulr_folder(self, user_id):
        """Folder 모드용 (하위 호환성)"""
        self.process_ulr(user_id)
    
    def process_ulr_adb(self, user_id):
        """ADB 모드용 (하위 호환성)"""
        self.process_ulr(user_id)

    def parse_err_rst_stat(self, content):
        """eRR.p ??RST_STAT ?????? ??? ??? (KST)"""
        if not content:
            return []
        matches = []
        pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\+?(\d{4})?.*?RST_STAT", re.IGNORECASE)
        for line in content.splitlines():
            m = pattern.search(line)
            if not m:
                continue
            dt_str = m.group(1)
            try:
                dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                matches.append((dt_str, dt_obj))
            except Exception:
                continue
        return matches

    def search_zip(self, target_file):
        """ZIP 파일 또는 해제된 폴더에서 파일 검색"""
        try:
            if self.choice == "1":
                if target_file not in self.file_list:
                    self.log(f"[경로 후보] ZIP에 없음: {target_file}")
                    return None
                else:
                    return target_file
            elif self.choice == "3":
                actual_path = self.get_actual_path(target_file)
                if actual_path and os.path.exists(actual_path):
                    return actual_path
                else:
                    # 경로 후보 로깅
                    candidates = [os.path.join(self.base_path, target_file)]
                    if isinstance(target_file, str) and target_file.startswith("Dump/"):
                        candidates.append(os.path.join(self.base_path, target_file[len("Dump/"):]))
                    self.log(f"[경로 후보] 파일 없음: {target_file}")
                    for cand in candidates:
                        self.log(f"  - {cand}")
                    return None
            else:
                return None
        except Exception as e:
            self.log(f"파일 검색 중 오류({e})")
            return None
    
    def get_actual_path(self, logical_path):
        """logical 경로를 실제 파일 시스템 경로로 변환"""
        if not self.base_path:
            return None
        actual_path = os.path.join(self.base_path, logical_path)
        if os.path.exists(actual_path):
            return actual_path
        # Dump/ 접두어가 없는 폴더 구조 대응
        if isinstance(logical_path, str) and logical_path.startswith("Dump/"):
            alt_path = os.path.join(self.base_path, logical_path[len("Dump/"):])
            if os.path.exists(alt_path):
                return alt_path
        return actual_path

    def log_parse_failure(self, file_path, reason, content=None):
        """파싱 실패 원인 상세 로그"""
        self.log(f"[파싱 실패] {file_path} - {reason}")
        if content:
            snippet = content[:300].replace("\n", "\\n")
            self.log(f"  내용 미리보기: {snippet}...")

    def read_file(self, target_file):
        """ZIP 파일 또는 해제된 폴더에서 파일 읽기"""
        try:
            if self.choice == "1":
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    if target_file not in zip_ref.namelist():
                        return None
                    with zip_ref.open(target_file) as file:
                        raw = file.read()
                        for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                            try:
                                return raw.decode(enc)
                            except Exception:
                                continue
                        return raw.decode("utf-8", errors="ignore")
            elif self.choice == "3":
                actual_path = self.get_actual_path(target_file) if isinstance(target_file, str) and not os.path.isabs(target_file) else target_file
                if not actual_path or not os.path.exists(actual_path):
                    return None
                with open(actual_path, 'rb') as file:
                    raw = file.read()
                for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16le", "utf-16be"):
                    try:
                        return raw.decode(enc)
                    except Exception:
                        continue
                return raw.decode("utf-8", errors="ignore")
            else:
                return None
        except Exception as e:
            self.log(f"파일 {target_file}을(를) 읽을 수 없습니다. {e}")
            return None

    def read_file_bytes(self, target_file):
        """ZIP 파일 또는 해제된 폴더에서 파일을 bytes로 읽기"""
        try:
            if self.choice == "1":
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    if target_file not in zip_ref.namelist():
                        return None
                    with zip_ref.open(target_file) as file:
                        return file.read()
            elif self.choice == "3":
                actual_path = self.get_actual_path(target_file) if isinstance(target_file, str) and not os.path.isabs(target_file) else target_file
                if not actual_path or not os.path.exists(actual_path):
                    return None
                with open(actual_path, "rb") as f:
                    return f.read()
            else:
                return None
        except Exception as e:
            self.log(f"파일 {target_file} bytes 읽기 실패: {e}")
            return None

    def parse_xiaomi_last_log_timeline(self, content_text):
        """Xiaomi(MIUI) last_log에서 타임라인 파싱"""
        if not content_text:
            return None

        base_dt = None
        base_rel = None
        for line in content_text.splitlines():
            if "get_system_time=" not in line:
                continue
            m = re.search(r'^\[\s*(\d+\.\d+)\]\s+.*get_system_time=(\d{4}-\d{2}-\d{2})-(\d{2}:\d{2}:\d{2})', line)
            if m:
                base_rel = float(m.group(1))
                base_dt = datetime.strptime(f"{m.group(2)} {m.group(3)}", "%Y-%m-%d %H:%M:%S")
                break

        if base_dt is None or base_rel is None:
            return None

        keywords = [
            "get_system_time=",
            "-- Wiping data",
            "Formatting /data",
            "Info: format successful",
            "Data wipe complete",
            "Saving new_status",
            "enter finish_recovery",
        ]

        timeline = []
        for line in content_text.splitlines():
            m = re.match(r'^\[\s*(\d+\.\d+)\]\s+(.*)$', line)
            if not m:
                continue
            rel = float(m.group(1))
            msg = m.group(2)
            if any(k in msg for k in keywords):
                abs_dt = base_dt + timedelta(seconds=(rel - base_rel))
                abs_str = abs_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " KST"
                timeline.append((abs_str, rel, msg))

        return {
            "base_dt": base_dt,
            "base_rel": base_rel,
            "timeline": timeline,
        }

    def get_mod_time_from_zip(self, target_file):
        """ZIP 파일 또는 해제된 폴더에서 파일 수정 시간 가져오기"""
        try:
            if self.choice == "1":
                if target_file not in self.file_list:
                    self.log(f"{target_file}이 없습니다")
                    return None
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    info = zip_ref.getinfo(target_file)
                mod_time = datetime(*info.date_time)
                return mod_time
            elif self.choice == "3":
                actual_path = self.get_actual_path(target_file) if isinstance(target_file, str) and not os.path.isabs(target_file) else target_file
                if not actual_path or not os.path.exists(actual_path):
                    self.log(f"{target_file}이 없습니다")
                    return None
                mod_time = datetime.fromtimestamp(os.path.getmtime(actual_path))
                return mod_time
            else:
                return None
        except Exception as e:
            self.log(f"파일 수정 시간 가져오기 중 오류: {e}")
            return None

    def search_timestamp_in_property(self, target_file, pattern):
        """ZIP 파일 또는 해제된 폴더에서 타임스탬프 검색"""
        try:
            content = None
            if self.choice == "1":
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    if target_file not in zip_ref.namelist():
                        return None, None
                    with zip_ref.open(target_file) as file:
                        raw_bytes = file.read()
                        # 여러 인코딩 시도
                        for enc in ['utf-8-sig', 'utf-8', 'cp949', 'latin-1']:
                            try:
                                content = raw_bytes.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        if content is None:
                            # 모든 인코딩 실패 시 errors='ignore'로 시도
                            content = raw_bytes.decode('utf-8', errors='ignore')
            elif self.choice == "3":
                actual_path = self.get_actual_path(target_file) if isinstance(target_file, str) and not os.path.isabs(target_file) else target_file
                if not actual_path or not os.path.exists(actual_path):
                    return None, None
                # 여러 인코딩 시도
                for enc in ['utf-8-sig', 'utf-8', 'cp949', 'latin-1']:
                    try:
                        with open(actual_path, 'r', encoding=enc) as file:
                            content = file.read()
                        break
                    except (UnicodeDecodeError, FileNotFoundError):
                        continue
                if content is None:
                    # 모든 인코딩 실패 시 errors='ignore'로 시도
                    try:
                        with open(actual_path, 'r', encoding='utf-8', errors='ignore') as file:
                            content = file.read()
                    except Exception:
                        return None, None
            else:
                return None, None
            
            if content is None:
                return None, None
            
            extracted_values = {}
            matches = re.findall(pattern, content)
            if matches:
                extracted_values[target_file] = matches
            else:
                self.log("no matches in property\n")
            return extracted_values, matches
        except Exception as e:
            self.log(f"파일 {target_file}을(를) 찾을 수 없습니다. {e}")
            return None, None

    def extract_from_binary_xml(self, target_file, adb_mode=False):
        pattern_pixel = r'<pkg[^>]*n="com\.google\.android\.(?:pixel\.)?setupwizard"[^>]*>.*?<st[^>]*\br="(\d+)"'
        pattern_galaxy = r'<pkg[^>]*n="com\.sec\.android\.app\.?SecSetupWizard"[^>]*>.*?<st[^>]*\br="(\d+)"'
        script_name = 'ccl_abx.py'
        try:
            if adb_mode:
                binary_content = self.adb_read_binary_file(target_file)
            elif self.choice == "1":
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    if target_file not in zip_ref.namelist():
                        self.log(f"파일 {target_file}이(가) ZIP 파일에 존재하지 않습니다.")
                        return None
                    with zip_ref.open(target_file) as file:
                        binary_content = file.read()
            elif self.choice == "3":
                actual_path = self.get_actual_path(target_file) if isinstance(target_file, str) and not os.path.isabs(target_file) else target_file
                if not actual_path or not os.path.exists(actual_path):
                    self.log(f"파일 {target_file}이(가) 폴더에 존재하지 않습니다.")
                    return None
                with open(actual_path, "rb") as file:
                    binary_content = file.read()
            else:
                return None
            
            if not os.path.exists(script_name):
                self.log(f"경고: {script_name} 파일을 찾을 수 없습니다. appops.xml 처리를 건너뜁니다.")
                return None
            
            with open("temp_binary_file", "wb") as temp_file:
                temp_file.write(binary_content)
            
            python_cmd = "python"
            try:
                subprocess.run([python_cmd, "--version"], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                python_cmd = "python3"
                try:
                    subprocess.run([python_cmd, "--version"], capture_output=True, check=True)
                except (subprocess.CalledProcessError, FileNotFoundError):
                    self.log(f"경고: Python을 찾을 수 없습니다. appops.xml 처리를 건너뜁니다.")
                    return None
            
            command = [python_cmd, script_name, "temp_binary_file"]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            
            if result.returncode != 0:
                self.log(f"ccl_abx.py 실행 실패 (exit code: {result.returncode})")
                if result.stderr:
                    self.log(f"오류 메시지: {result.stderr}")
                if result.stdout:
                    self.log(f"출력: {result.stdout}")
                self.last_abx_output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
                return None
            
            results = result.stdout.strip()
            if not results:
                self.log("ccl_abx.py가 출력을 생성하지 않았습니다.")
                self.last_abx_output = "ccl_abx.py 출력 없음"
                return None
            self.last_abx_output = results
                
            matches = re.findall(pattern_pixel, results, re.DOTALL | re.IGNORECASE)
            if not matches:
                matches = re.findall(pattern_galaxy, results, re.DOTALL | re.IGNORECASE)
            if not matches:
                # fallback: 모든 r="숫자" 패턴
                matches = re.findall(r'\br="(\d+)"', results)
            timestamps = []
            for match in matches:
                if isinstance(match, tuple):
                    timestamps.append(match[0])
                else:
                    timestamps.append(match)
            if timestamps:
                self.log("추출된 값: " + str(matches))
            else:
                preview = results[:500].replace("\n", "\\n")
                self.log(f"ccl_abx.py 출력에 매칭 없음. 미리보기: {preview}...")
            return timestamps
        except zipfile.BadZipFile as e:
            self.log(f"Invalid ZIP file: {e}")
            return None
        except Exception as e:
            self.log(f"오류 발생: {e}")
            return None

    def timestamp_process(self, value, artifact_id=None, path=None, name=None, original_time=None, is_kst=None):
        """타임스탬프 처리 및 GUI에 데이터 추가"""
        result_time = None
        if original_time is None:
            original_time = value  # 원본 시간이 지정되지 않으면 value 사용
        if isinstance(value, datetime):
            # 이미 datetime인 경우
            # bootstat는 이미 KST이므로 is_kst=True로 표시
            is_kst = (artifact_id == "1")
            if is_kst:
                result_time = value  # KST로 간주
                self.log(f"Datetime (KST): {value}")
            else:
                result_time = value  # UTC로 간주
                self.log(f"Datetime (UTC): {value}")
        else:
            try:
                epoch_value = int(value)
                if epoch_value > 253402300799:
                    epoch_value /= 1000
                result_time = datetime.utcfromtimestamp(epoch_value)
                self.log(f"Epoch value: {value} -> UTC: {result_time}")
            except (ValueError, OverflowError) as e:
                self.log(f"Invalid or out-of-range epoch value: {value}. Error: {e}")

            # Epoch 파싱이 실패했을 때만 ISO 형식 시도
            if result_time is None and isinstance(value, str):
                iso_candidate = value.strip()
                if iso_candidate.endswith("Z"):
                    iso_candidate = iso_candidate.replace("Z", "+00:00")
                # 날짜/시간 형식 가능성이 있는 문자열만 시도
                if any(ch in iso_candidate for ch in ("-", "T", ":", "+")):
                    try:
                        result_time = datetime.fromisoformat(iso_candidate)
                        self.log(f"UTC timestamp: {iso_candidate} -> UTC: {result_time}")
                    except ValueError as e2:
                        self.log(f"Invalid UTC timestamp: {iso_candidate}. Error: {e2}")

        # GUI에 데이터 추가
        if result_time and artifact_id and self.gui_instance:
            display_name = name if name else "알 수 없음"
            display_path = path if path else ""
            # is_kst가 명시적으로 지정되지 않으면 기본 규칙 적용
            if is_kst is None:
                # bootstat는 KST, recovery.log와 last_log는 UTC 0
                is_kst = (artifact_id == "1")
                if artifact_id in ["21", "22"]:  # recovery.log와 last_log는 UTC 0
                    is_kst = False
            self.gui_instance.add_artifact_data(
                artifact_id,
                display_name,
                display_path,
                result_time,
                None,
                is_kst=is_kst,
                original_time=original_time
            )

        return result_time
    
    def collect_folder_files(self, folder_path):
        """폴더 내 모든 파일의 logical 경로 수집"""
        file_list = []
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                full_path = os.path.join(root, file)
                logical_path = os.path.relpath(full_path, folder_path)
                logical_path = logical_path.replace('\\', '/')
                file_list.append(logical_path)
        return file_list

    def get_user_path(self):
        if self.choice == "1":
            user_ids = set()
            for file in self.file_list:
                if file.startswith("Dump/data/user/"):
                    parts = file.split('/')
                    if len(parts) > 3:
                        user_ids.add(parts[3])
            if user_ids:
                self.log(f"추출된 USER 값: {user_ids}")
                return list(user_ids)[-1]
            else:
                self.log("ZIP 파일에서 사용자 정보를 찾을 수 없습니다.")
                return None
        elif self.choice == "3":
            user_ids = set()
            for file in self.file_list:
                if file.startswith("Dump/data/user/") or file.startswith("data/user/"):
                    parts = file.split('/')
                    if "user" in parts:
                        user_idx = parts.index("user")
                        if user_idx + 1 < len(parts):
                            user_ids.add(parts[user_idx + 1])
            if user_ids:
                self.log(f"추출된 USER 값: {user_ids}")
                return list(user_ids)[-1]
            else:
                self.log("폴더에서 사용자 정보를 찾을 수 없습니다.")
                return None
        elif self.choice == "2":
            try:
                result = subprocess.check_output(self.get_adb_args('shell', 'ls', '/data/user/'), text=True)
                user_ids = result.strip().split()
                user_id = user_ids[0] if user_ids else None
                if not user_id:
                    raise ValueError("사용자 ID를 확인할 수 없습니다.")
                return user_id
            except subprocess.CalledProcessError as e:
                self.log(f"ADB 명령 실행 실패: {e}")
                return None

    # ----------------- ADB 전용 헬퍼 함수 -----------------
    def find_adb_path(self):
        """ADB 실행 파일 경로 찾기"""
        # 먼저 PATH에서 adb 찾기 시도
        try:
            if os.name == 'nt':  # Windows
                result = subprocess.run(["where", "adb"], 
                                      stdout=subprocess.PIPE, 
                                      stderr=subprocess.PIPE, 
                                      text=True, 
                                      timeout=3)
            else:  # Linux/Mac
                result = subprocess.run(["which", "adb"], 
                                      stdout=subprocess.PIPE, 
                                      stderr=subprocess.PIPE, 
                                      text=True, 
                                      timeout=3)
            
            if result.returncode == 0 and result.stdout.strip():
                adb_path = result.stdout.strip().split('\n')[0]
                if os.path.exists(adb_path):
                    return adb_path
        except Exception:
            pass
        
        # 일반적인 Android SDK 경로 확인 (Windows)
        if os.name == 'nt':
            common_paths = [
                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
                os.path.join(os.environ.get('ProgramFiles', ''), 'Android', 'android-sdk', 'platform-tools', 'adb.exe'),
                os.path.join(os.environ.get('ProgramFiles(x86)', ''), 'Android', 'android-sdk', 'platform-tools', 'adb.exe'),
                os.path.join(os.environ.get('USERPROFILE', ''), 'AppData', 'Local', 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
            ]
            for path in common_paths:
                if path and os.path.exists(path):
                    return path
        
        # 일반적인 경로 확인 (Linux/Mac)
        else:
            common_paths = [
                os.path.expanduser('~/Android/Sdk/platform-tools/adb'),
                os.path.expanduser('~/Library/Android/sdk/platform-tools/adb'),
                '/usr/local/bin/adb',
                '/usr/bin/adb',
            ]
            for path in common_paths:
                if os.path.exists(path):
                    return path
        
        return None
    
    def get_adb_command(self):
        """ADB 명령어 반환 (경로 포함)"""
        adb_path = self.find_adb_path()
        if adb_path:
            return adb_path
        # 경로를 찾지 못하면 'adb'만 반환 (PATH에 있을 수 있음)
        return "adb"
    
    def get_adb_args(self, *args):
        """ADB 명령 인자 생성 (여러 디바이스가 있을 때 -s 옵션 추가)"""
        adb_cmd = self.get_adb_command()
        cmd_list = [adb_cmd]
        # 여러 디바이스가 있을 때 디바이스 ID 지정
        if self.adb_device_id:
            cmd_list.extend(["-s", self.adb_device_id])
        cmd_list.extend(args)
        return cmd_list
    
    def check_adb_connection(self):
        """ADB 연결 상태 확인"""
        adb_cmd = self.get_adb_command()
        
        # ADB 실행 파일이 존재하는지 확인
        if adb_cmd != "adb" and not os.path.exists(adb_cmd):
            self.log("=" * 60)
            self.log("오류: ADB를 찾을 수 없습니다.")
            self.log("=" * 60)
            self.log("ADB를 설치하거나 PATH에 추가해주세요.")
            self.log("")
            self.log("Windows에서 ADB 설치 방법:")
            self.log("1. Android SDK Platform-Tools 다운로드:")
            self.log("   https://developer.android.com/studio/releases/platform-tools")
            self.log("2. 다운로드한 platform-tools 폴더의 adb.exe 경로를 PATH에 추가")
            self.log("3. 또는 adb.exe가 있는 폴더 경로를 환경 변수에 추가")
            self.log("")
            self.log("일반적인 ADB 경로:")
            self.log("- %LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe")
            self.log("- %USERPROFILE%\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe")
            self.log("=" * 60)
            return False
        
        try:
            result = subprocess.run([adb_cmd, "devices"], 
                                  stdout=subprocess.PIPE, 
                                  stderr=subprocess.PIPE, 
                                  text=True, 
                                  timeout=5)
            if result.returncode != 0:
                self.log(f"ADB 명령 실행 실패: {result.stderr}")
                return False
            
            # devices 명령 출력에서 실제 연결된 디바이스 확인
            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                self.log("연결된 디바이스가 없습니다.")
                self.log("USB 디버깅이 활성화되어 있고 디바이스가 연결되어 있는지 확인하세요.")
                return False
            
            # "device" 또는 "unauthorized" 상태 확인
            devices_found = False
            device_list = []
            for line in lines[1:]:
                if line.strip():
                    parts = line.strip().split('\t')
                    if len(parts) >= 2:
                        device_id = parts[0]
                        status = parts[1]
                        if status == 'device':
                            devices_found = True
                            device_list.append(device_id)
                            self.log(f"디바이스 발견: {device_id} ({status})")
                        elif 'unauthorized' in status:
                            self.log(f"디바이스 인증 필요: {device_id} ({status})")
                            self.log("디바이스에서 USB 디버깅 권한을 허용해주세요.")
            
            # 여러 디바이스가 있을 때 첫 번째 디바이스 선택
            if len(device_list) > 1:
                self.adb_device_id = device_list[0]
                self.log(f"여러 디바이스가 연결되어 있습니다. 첫 번째 디바이스 사용: {self.adb_device_id}")
            elif len(device_list) == 1:
                self.adb_device_id = device_list[0]
            
            return devices_found
        except subprocess.TimeoutExpired:
            self.log("ADB 연결 확인 시간 초과")
            return False
        except FileNotFoundError:
            self.log("=" * 60)
            self.log("오류: ADB를 찾을 수 없습니다.")
            self.log("=" * 60)
            self.log("ADB가 설치되어 있고 PATH에 추가되어 있는지 확인하세요.")
            return False
        except Exception as e:
            self.log(f"ADB 연결 확인 중 오류: {e}")
            return False
    
    def check_root_access(self):
        """루트 권한 확인"""
        adb_cmd = self.get_adb_command()
        try:
            # su 명령으로 id 확인 (루트면 uid=0)
            result = subprocess.run([adb_cmd, "shell", "su", "-c", "id"],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  text=True,
                                  timeout=5)
            
            if result.returncode == 0:
                # uid=0이면 루트 권한
                if "uid=0" in result.stdout:
                    self.log("루트 권한 확인됨.")
                    return True
                else:
                    self.log(f"루트 권한 없음. 현재 사용자: {result.stdout.strip()}")
                    return False
            else:
                # su 명령 실패 (루트 권한 없음 또는 su가 거부됨)
                error_msg = result.stderr.strip() if result.stderr else "알 수 없는 오류"
                if "not found" in error_msg.lower() or "permission denied" in error_msg.lower():
                    self.log("루트 권한 없음: su 명령을 실행할 수 없습니다.")
                else:
                    self.log(f"루트 권한 확인 실패: {error_msg}")
                return False
        except (subprocess.TimeoutExpired, Exception) as e:
            self.log(f"루트 권한 확인 중 오류: {e}")
            return False
    
    def adb_file_exists(self, file_path):
        """ADB를 통해 파일 존재 여부 확인 (루트 권한 필요)"""
        adb_cmd = self.get_adb_command()
        try:
            result = subprocess.run([adb_cmd, "shell", "su", "-c", f"test -f {file_path} && echo 'exists'"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            if result.returncode == 0 and "exists" in result.stdout:
                return True
            # 대체 방법: ls 명령 사용
            result = subprocess.run([adb_cmd, "shell", "su", "-c", f"ls {file_path}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            if result.returncode == 0 and "No such file" not in result.stderr:
                return True
            return False
        except subprocess.TimeoutExpired:
            self.log(f"파일 존재 확인 시간 초과: {file_path}")
            return False
        except Exception as e:
            self.log(f"파일 존재 확인 중 오류 ({file_path}): {e}")
            return False

    def adb_read_file(self, file_path, decode='utf-8'):
        """ADB를 통해 파일 읽기 (루트 권한 필요)"""
        adb_cmd = self.get_adb_command()
        try:
            result = subprocess.run([adb_cmd, "shell", "su", "-c", f"cat {file_path}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if result.stderr and result.stderr.strip():
                # stderr에 실제 오류가 있는 경우만 로그
                if "Permission denied" in result.stderr or "No such file" not in result.stderr:
                    self.log(f"파일 읽기 경고 ({file_path}): {result.stderr.strip()}")
            return result.stdout
        except subprocess.TimeoutExpired:
            self.log(f"파일 읽기 시간 초과: {file_path}")
            return ""
        except Exception as e:
            self.log(f"파일 읽기 오류 ({file_path}): {e}")
            return ""
    
    def adb_read_file_bytes(self, file_path):
        """ADB를 통해 파일을 바이트로 읽기 (루트 권한 필요)"""
        adb_cmd = self.get_adb_command()
        try:
            result = subprocess.run([adb_cmd, "shell", "su", "-c", f"cat {file_path}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if result.stderr and result.stderr.strip():
                # stderr에 실제 오류가 있는 경우만 로그
                if "Permission denied" in result.stderr or "No such file" not in result.stderr:
                    self.log(f"파일 읽기 경고 ({file_path}): {result.stderr.strip()}")
            return result.stdout
        except subprocess.TimeoutExpired:
            self.log(f"파일 읽기 시간 초과: {file_path}")
            return b""
        except Exception as e:
            self.log(f"파일 읽기 오류 ({file_path}): {e}")
            return b""

    def adb_read_binary_file(self, file_path):
        """ADB를 통해 바이너리 파일 읽기 (루트 권한 필요)"""
        adb_cmd = self.get_adb_command()
        try:
            result = subprocess.run([adb_cmd, "shell", "su", "-c", f"cat {file_path}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if result.stderr:
                error_msg = result.stderr.decode('utf-8', errors='ignore')
                if "Permission denied" in error_msg or ("No such file" not in error_msg and error_msg.strip()):
                    self.log(f"바이너리 파일 읽기 경고 ({file_path}): {error_msg.strip()}")
            return result.stdout
        except subprocess.TimeoutExpired:
            self.log(f"바이너리 파일 읽기 시간 초과: {file_path}")
            return None
        except Exception as e:
            self.log(f"바이너리 파일 읽기 중 오류 ({file_path}): {e}")
            return None

    def adb_get_mod_time(self, file_path):
        adb_cmd = self.get_adb_command()
        result = subprocess.run([adb_cmd, "shell", "su", "-c", f"stat {file_path}"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.stderr:
            self.log(f"Error stat-ing {file_path}: {result.stderr}")
            return None
        for line in result.stdout.splitlines():
            if "Modify:" in line:
                match = re.search(r"Modify:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if match:
                    date_str = match.group(1)
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    return dt
        return None

    def adb_pull_file(self, remote_path, local_path):
        """ADB를 통해 파일 pull (루트 권한이 필요한 파일의 경우 임시로 복사 후 pull)"""
        adb_cmd = self.get_adb_command()
        temp_path = "/data/local/tmp/temp_file"
        try:
            # root 권한으로 임시 파일 생성
            copy_result = subprocess.run([adb_cmd, "shell", "su", "-c", f"cp {remote_path} {temp_path}"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

            if copy_result.returncode == 0:
                # 임시 파일 pull
                pull_result = subprocess.run([adb_cmd, "pull", temp_path, local_path],
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                # 임시 파일 삭제
                subprocess.run([adb_cmd, "shell", "su", "-c", f"rm {temp_path}"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

                if pull_result.returncode == 0:
                    return True
                else:
                    self.log(f"파일 pull 실패 ({remote_path}): {pull_result.stderr}")
                    return False
            else:
                # 복사 실패 시 직접 pull 시도
                pull_result = subprocess.run([adb_cmd, "pull", remote_path, local_path],
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                if pull_result.returncode == 0:
                    return True
                else:
                    self.log(f"파일 pull 실패 ({remote_path}): {pull_result.stderr}")
                    return False
        except subprocess.TimeoutExpired:
            self.log(f"파일 pull 시간 초과: {remote_path}")
            return False
        except Exception as e:
            self.log(f"파일 pull 중 오류 ({remote_path}): {e}")
            return False

    def execute_wellbing_query_local(self, db_path, query):
        try:
            import sqlite3
        except ImportError as e:
            error_msg = str(e)
            if "DLL" in error_msg or "_sqlite3" in error_msg:
                self.log("******************************************")
                self.log("[오류] SQLite 모듈을 불러올 수 없습니다.")
                self.log("Python 환경의 SQLite DLL이 손상되었거나 누락되었습니다.")
                self.log("해결 방법:")
                self.log("1. Python 환경을 재설정하거나")
                self.log("2. 다른 Python 환경을 사용하거나")
                self.log("3. wellbing/internal 기능을 사용하지 않도록 선택하세요.")
                self.log("******************************************")
            else:
                self.log(f"SQLite 모듈을 불러올 수 없습니다: {e}")
            return None
        except Exception as e:
            self.log(f"SQLite 모듈 로드 중 예상치 못한 오류: {e}")
            return None
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(query)
            columns = [description[0] for description in cursor.description]
            results = cursor.fetchall()
            df = pd.DataFrame(results, columns=columns)
            conn.close()
            return df
        except Exception as e:
            self.log(f"Error in execute_wellbing_query_local: {e}")
            return None

    def execute_internal_query_local(self, db_path):
        try:
            import sqlite3
        except ImportError as e:
            error_msg = str(e)
            if "DLL" in error_msg or "_sqlite3" in error_msg:
                self.log("******************************************")
                self.log("[오류] SQLite 모듈을 불러올 수 없습니다.")
                self.log("Python 환경의 SQLite DLL이 손상되었거나 누락되었습니다.")
                self.log("해결 방법:")
                self.log("1. Python 환경을 재설정하거나")
                self.log("2. 다른 Python 환경을 사용하거나")
                self.log("3. wellbing/internal 기능을 사용하지 않도록 선택하세요.")
                self.log("******************************************")
            else:
                self.log(f"SQLite 모듈을 불러올 수 없습니다: {e}")
            return None
        except Exception as e:
            self.log(f"SQLite 모듈 로드 중 예상치 못한 오류: {e}")
            return None
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT MIN(date_added) AS earliest_date FROM files;")
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
        except Exception as e:
            self.log(f"Error in execute_internal_query_local: {e}")
            return None

    # ----------------- 기존 execute_wellbing_query (ZIP/폴더 모드) -----------------
    def execute_wellbing_query(self, db_file, query):
        try:
            import sqlite3
        except ImportError as e:
            error_msg = str(e)
            if "DLL" in error_msg or "_sqlite3" in error_msg:
                self.log("******************************************")
                self.log("[오류] SQLite 모듈을 불러올 수 없습니다.")
                self.log("Python 환경의 SQLite DLL이 손상되었거나 누락되었습니다.")
                self.log("해결 방법:")
                self.log("1. Python 환경을 재설정하거나")
                self.log("2. 다른 Python 환경을 사용하거나")
                self.log("3. wellbing/internal 기능을 사용하지 않도록 선택하세요.")
                self.log("******************************************")
                return "SQLite 모듈을 사용할 수 없습니다."
            else:
                self.log(f"SQLite 모듈을 불러올 수 없습니다: {e}")
                return "SQLite 모듈을 사용할 수 없습니다."
        except Exception as e:
            self.log(f"SQLite 모듈 로드 중 예상치 못한 오류: {e}")
            return "SQLite 모듈을 사용할 수 없습니다."
        
        try:
            if self.choice == "1":
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    with zip_ref.open(db_file) as file:
                        db_content = file.read()
            elif self.choice == "3":
                actual_path = self.get_actual_path(db_file) if isinstance(db_file, str) and not os.path.isabs(db_file) else db_file
                if not actual_path or not os.path.exists(actual_path):
                    raise FileNotFoundError(f"데이터베이스 파일을 찾을 수 없습니다: {db_file}")
                with open(actual_path, "rb") as file:
                    db_content = file.read()
            else:
                return None
            
            with open("temp_db.db", "wb") as temp_file:
                temp_file.write(db_content)
            mem_db = sqlite3.connect(":memory:")
            disk_db = sqlite3.connect("temp_db.db")
            with disk_db:
                disk_db.backup(mem_db)
            cursor = mem_db.cursor()
            if query is None:
                cursor.execute("SELECT MIN(date_added) AS earliest_date FROM files;")
                result = cursor.fetchone()
                mem_db.close()
                disk_db.close()
                return result[0] if result else None
            if "6" in self.artifact_choices or "0" in self.artifact_choices:
                cursor.execute(query)
                columns = [description[0] for description in cursor.description]
                results = cursor.fetchall()
                df = pd.DataFrame(results, columns=columns)
                try:
                    filtered_df = df[df["package_name"].isin(["com.google.android.setupwizard", "android"])]
                except Exception as e:
                    filtered_df = df[df["name"].isin(["setupwizard", "android"])]
                mem_db.close()
                disk_db.close()
                return filtered_df
            else:
                cursor.execute("SELECT MIN(date_added) AS earliest_date FROM files;")
                result = cursor.fetchone()
                mem_db.close()
                disk_db.close()
                return result[0]
        except sqlite3.Error as e:
            self.log(f"SQLite 오류 발생: {e}")
            return "wellbeing 데이터가 기록되지 않았습니다."
        except Exception as e:
            self.log(f"오류 발생: {e}")
            return None
    
    def deep_search(self, search_times, result_callback, progress_callback=None, time_tolerance_seconds=300):
        """Deep search - search files using extracted times
        
        Args:
            search_times: 검색할 시간 정보 리스트
            result_callback: 결과를 전달할 콜백 함수
            progress_callback: 진행률을 전달할 콜백 함수
            time_tolerance_seconds: 시간 매칭 오차 허용 범위 (초, 기본값: 300초 = 5분)
        """
        # Pre-compile regex patterns to avoid recursion issues
        time_pattern = re.compile(r"\d{2}:\d{2}:\d{2}")
        
        self.log("=" * 60)
        self.log("Deep Search started")
        self.log(f"시간 매칭 오차 허용 범위: ±{time_tolerance_seconds}초 (±{time_tolerance_seconds/60:.1f}분)")
        self.log("=" * 60)
        
        # 검색할 시간 형식 생성
        search_patterns = []
        for time_info in search_times:
            time_dt = time_info['time']
            original_time = time_info.get('original_time')
            
            # 여러 형식으로 변환
            patterns = {}
            
            # 1. Epoch (초)
            epoch_sec = int(time_dt.timestamp())
            patterns['epoch_sec'] = str(epoch_sec)
            
            # 2. Epoch (밀리초)
            epoch_ms = int(time_dt.timestamp() * 1000)
            patterns['epoch_ms'] = str(epoch_ms)
            
            # 3. 날짜 형식들
            patterns['date_iso'] = time_dt.strftime('%Y-%m-%d %H:%M:%S')
            patterns['date_slash'] = time_dt.strftime('%Y/%m/%d %H:%M:%S')
            patterns['date_dot'] = time_dt.strftime('%Y.%m.%d %H:%M:%S')
            patterns['date_only'] = time_dt.strftime('%Y-%m-%d')
            
            # 4. 원본 시간 형식
            if original_time:
                if isinstance(original_time, datetime):
                    patterns['original_datetime'] = original_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    patterns['original_value'] = str(original_time)
            
            search_patterns.append({
                'time_str': time_dt.strftime('%Y-%m-%d %H:%M:%S UTC'),
                'patterns': patterns,
                'time_info': time_info
            })
        
        # 파일 목록 가져오기
        if self.choice == "1":
            files_to_search = self.file_list
        elif self.choice == "3":
            files_to_search = self.file_list
        elif self.choice == "2":
            # ADB 모드에서는 주요 경로의 파일들 검색
            files_to_search = self.get_adb_file_list()
        else:
            files_to_search = []
        
        # 바이너리 파일 필터링
        text_files = []
        skip_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.mp4', '.mp3', '.apk', '.so', '.dex', '.bin', '.dat', '.zip', '.rar']
        for file_path in files_to_search:
            if not any(file_path.lower().endswith(ext) for ext in skip_extensions):
                text_files.append(file_path)
        
        total_files = len(text_files)
        self.log(f"검색할 파일 수: {total_files}")
        self.log(f"검색할 시간 패턴 수: {len(search_patterns)}")
        
        match_count = 0
        processed_count = 0
        
        # 각 파일에서 검색
        for idx, file_path in enumerate(text_files):
            try:
                # 파일 읽기
                if self.choice == "1":
                    content = self.read_file_for_search(file_path)
                    raw_bytes = self.read_file_bytes(file_path)
                elif self.choice == "3":
                    content = self.read_file_for_search(file_path)
                    raw_bytes = self.read_file_bytes(file_path)
                elif self.choice == "2":
                    content = self.adb_read_file_for_search(file_path)
                    raw_bytes = self.adb_read_file_bytes(file_path)
                else:
                    content = None
                    raw_bytes = b""
                
                processed_count += 1
                
                # 진행률 업데이트 (10개마다 또는 마지막 파일)
                if progress_callback and (processed_count % 10 == 0 or processed_count == total_files):
                    progress_callback.emit(processed_count, total_files)
                
                if not content:
                    continue
                content_lower = content.lower()

                # 파일 수정 시간 기반 매칭
                file_mtime = self.get_file_mod_time_for_search(file_path)
                if file_mtime:
                    for search_info in search_patterns:
                        time_dt = search_info['time_info']['time']
                        diff_sec = abs((file_mtime - time_dt).total_seconds())
                        if diff_sec <= time_tolerance_seconds:
                            match_count += 1
                            diff_min = diff_sec / 60
                            if diff_sec < 60:
                                display_value = f"{file_mtime.strftime('%Y-%m-%d %H:%M:%S')} (차이: {diff_sec:.0f}초)"
                            else:
                                display_value = f"{file_mtime.strftime('%Y-%m-%d %H:%M:%S')} (차이: {diff_min:.1f}분)"
                            result_callback.emit(
                                search_info['time_str'],
                                file_path,
                                "file_mtime",
                                display_value
                            )
                
                # 각 시간 패턴으로 검색
                for search_info in search_patterns:
                    for pattern_name, pattern_value in search_info['patterns'].items():
                        if not pattern_value:
                            continue
                        pattern_value_str = str(pattern_value)
                        pattern_value_lower = pattern_value_str.lower()
                        if pattern_value_lower in content_lower:
                            # 날짜만 매칭인데 실제로 시간 정보가 붙어 있는 경우는 날짜-only 결과를 건너뜀
                            if pattern_name == 'date_only':
                                idx = content_lower.find(pattern_value_lower)
                                if idx != -1:
                                    context = content_lower[max(0, idx - 3):idx + 20]
                                    # Use pre-compiled pattern to avoid recursion issues
                                    if time_pattern.search(context):
                                        continue
                            match_count += 1
                            
                            # 매칭된 형식에 따라 시간 정보 유무 표시
                            display_value = pattern_value_str
                            if pattern_name == 'date_only':
                                display_value = f"{pattern_value_str} (시간 없음)"
                            elif 'datetime' in pattern_name or 'iso' in pattern_name or 'slash' in pattern_name or 'dot' in pattern_name:
                                # 시간 정보가 포함된 형식인지 확인
                                if ':' not in pattern_value_str:
                                    display_value = f"{pattern_value_str} (시간 없음)"
                            
                            result_callback.emit(
                                search_info['time_str'],
                                file_path,
                                pattern_name,
                                display_value
                            )
                            self.log(f"매칭 발견: {file_path} - {pattern_name}: {display_value}")

                    # HEX/바이너리 패턴 검색
                    if raw_bytes:
                        bin_patterns = self.build_binary_patterns(search_info['time_info']['time'])
                        for bin_name, bin_value in bin_patterns.items():
                            offset = raw_bytes.find(bin_value)
                            if offset != -1:
                                match_count += 1
                                hex_str = " ".join(f"{b:02X}" for b in bin_value)
                                display_value = f"{bin_name} @0x{offset:X}: {hex_str}"
                                result_callback.emit(
                                    search_info['time_str'],
                                    file_path,
                                    f"hex_{bin_name}",
                                    display_value
                                )
                                self.log(f"매칭 발견(HEX): {file_path} - {bin_name} @0x{offset:X}")
            
            except Exception as e:
                # 파일 읽기 실패는 무시하고 계속
                processed_count += 1
                if progress_callback and (processed_count % 10 == 0 or processed_count == total_files):
                    progress_callback.emit(processed_count, total_files)
                continue
        
        # 최종 진행률 업데이트
        if progress_callback:
            progress_callback.emit(total_files, total_files)
        
        self.log(f"Deep Search completed. Total {match_count} matches found")
        self.log("=" * 60)
    
    def read_file_for_search(self, file_path):
        """검색용 파일 읽기 (텍스트 파일만)"""
        try:
            if self.choice == "1":
                with zipfile.ZipFile(self.zipfile, 'r') as zip_ref:
                    if file_path not in zip_ref.namelist():
                        return None
                    with zip_ref.open(file_path) as file:
                        try:
                            content = file.read().decode('utf-8', errors='ignore')
                            return content
                        except:
                            try:
                                content = file.read().decode('cp949', errors='ignore')
                                return content
                            except:
                                return None
            elif self.choice == "3":
                actual_path = self.get_actual_path(file_path) if isinstance(file_path, str) and not os.path.isabs(file_path) else file_path
                if not actual_path or not os.path.exists(actual_path):
                    return None
                try:
                    with open(actual_path, 'r', encoding='utf-8', errors='ignore') as f:
                        return f.read()
                except:
                    try:
                        with open(actual_path, 'r', encoding='cp949', errors='ignore') as f:
                            return f.read()
                    except:
                        return None
            return None
        except:
            return None
    
    def adb_read_file_for_search(self, file_path):
        """ADB 모드에서 검색용 파일 읽기"""
        try:
            content = self.adb_read_file(file_path)
            return content if content else None
        except:
            return None
    
    def get_adb_file_list(self):
        """ADB 모드에서 검색할 파일 목록 가져오기"""
        file_list = []
        search_paths = [
            "/data/data",
            "/data/system",
            "/data/misc",
            "/data/property",
            "/cache",
        ]
        
        for base_path in search_paths:
            try:
                result = subprocess.run(self.get_adb_args('shell', 'su', '-c', f'find {base_path} -type f 2>/dev/null | head -1000'),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                if result.returncode == 0:
                    files = result.stdout.strip().split('\n')
                    file_list.extend([f for f in files if f.strip()])
            except:
                continue
        
        return file_list[:5000]  # 최대 5000개 파일로 제한


class SavedResultsExplorer(QDialog):
    """파일 탐색기 스타일의 저장된 결과 뷰어"""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("저장된 분석 결과")
        self.setMinimumSize(1200, 800)
        self.results_dir = os.path.join(os.path.dirname(__file__), "saved_results")
        self.current_data = None
        self.current_filepath = None  # 현재 선택된 파일 경로
        self.init_ui()
        self.load_results()

    def _as_table_text(self, value):
        """Normalize values for QTableWidgetItem text constructor."""
        if value is None:
            return ""
        return str(value)
    
    def init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 상단 툴바
        toolbar = QHBoxLayout()
        btn_refresh = QPushButton("새로고침")
        btn_refresh.clicked.connect(self.load_results)
        btn_delete = QPushButton("삭제")
        btn_delete.clicked.connect(self.delete_selected)
        toolbar.addWidget(btn_refresh)
        toolbar.addWidget(btn_delete)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        # 분할 뷰 (왼쪽: 목록, 오른쪽: 상세)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        
        # 왼쪽: 결과 목록 (트리 뷰)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["결과 목록"])
        self.tree.setRootIsDecorated(True)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.itemDoubleClicked.connect(self.on_double_click)
        splitter.addWidget(self.tree)
        
        # 오른쪽: 상세 정보
        detail_widget = QWidget()
        detail_layout = QVBoxLayout()
        detail_widget.setLayout(detail_layout)
        
        # 편집 가능한 필드들
        edit_group = QGroupBox("상세 정보 편집")
        edit_layout = QVBoxLayout()
        edit_group.setLayout(edit_layout)
        
        # 차수
        order_layout = QHBoxLayout()
        order_layout.addWidget(QLabel("차수:"))
        self.order_edit = QLineEdit()
        self.order_edit.setPlaceholderText("예: 1차 또는 Ex1")
        order_layout.addWidget(self.order_edit)
        edit_layout.addLayout(order_layout)
        
        # 제조사
        manufacturer_layout = QHBoxLayout()
        manufacturer_layout.addWidget(QLabel("제조사:"))
        self.manufacturer_edit = QLineEdit()
        self.manufacturer_edit.setPlaceholderText("예: 삼성")
        manufacturer_layout.addWidget(self.manufacturer_edit)
        edit_layout.addLayout(manufacturer_layout)
        
        # 모델명
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("예: SM-S921N")
        model_layout.addWidget(self.model_edit)
        edit_layout.addLayout(model_layout)
        
        # 시나리오명
        scenario_layout = QHBoxLayout()
        scenario_layout.addWidget(QLabel("시나리오명:"))
        self.scenario_edit = QLineEdit()
        self.scenario_edit.setPlaceholderText("예: 공장초기화")
        scenario_layout.addWidget(self.scenario_edit)
        edit_layout.addLayout(scenario_layout)
        
        # 확정된 초기화 시간
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("확정된 초기화 시간:"))
        self.confirmed_time_edit = QLineEdit()
        self.confirmed_time_edit.setPlaceholderText("초기화 시간을 입력하세요")
        time_layout.addWidget(self.confirmed_time_edit)
        edit_layout.addLayout(time_layout)

        # 원본 소스/경로
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("원본 소스:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("ZIP", "1")
        self.source_combo.addItem("ADB", "2")
        self.source_combo.addItem("Folder", "3")
        source_layout.addWidget(self.source_combo)
        edit_layout.addLayout(source_layout)

        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("원본 파일 경로:"))
        self.original_path_edit = QLineEdit()
        self.original_path_edit.setPlaceholderText("예: D:/case/EXTRACTION_FFS.zip 또는 D:/case/folder")
        btn_browse_path = QPushButton("찾아보기")
        btn_browse_path.clicked.connect(self.browse_original_path)
        path_layout.addWidget(self.original_path_edit)
        path_layout.addWidget(btn_browse_path)
        edit_layout.addLayout(path_layout)
        
        # 메모
        memo_layout = QVBoxLayout()
        memo_layout.addWidget(QLabel("메모:"))
        self.memo_edit = QTextEdit()
        self.memo_edit.setPlaceholderText("메모를 입력하세요...")
        self.memo_edit.setMaximumHeight(100)
        memo_layout.addWidget(self.memo_edit)
        edit_layout.addLayout(memo_layout)
        
        # 저장 버튼
        btn_save = QPushButton("저장")
        btn_save.clicked.connect(self.save_edited_info)
        btn_save.setObjectName("primaryButton")
        edit_layout.addWidget(btn_save)
        
        detail_layout.addWidget(edit_group)
        
        # 읽기 전용 정보 (저장 시간, 원본 파일 등)
        info_group = QGroupBox("기본 정보 (읽기 전용)")
        info_layout = QVBoxLayout()
        info_group.setLayout(info_layout)
        
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setMaximumHeight(150)
        info_layout.addWidget(self.info_text)
        
        detail_layout.addWidget(info_group)
        
        # 상세 정보 탭
        self.detail_tabs = QTabWidget()
        detail_layout.addWidget(self.detail_tabs)
        
        # 아티팩트 결과 탭
        self.artifact_tabs = QTabWidget()
        self.detail_tabs.addTab(self.artifact_tabs, "아티팩트 결과")
        
        # Summary results tab
        self.summary_table = QTableWidget()
        self.summary_table.setColumnCount(4)
        self.summary_table.setHorizontalHeaderLabels(["Artifact", "Path", "Time", "Original Time"])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.summary_table.setSortingEnabled(True)
        self.detail_tabs.addTab(self.summary_table, "Summary Results")
        
        # Deep search results tab
        self.deep_search_table = QTableWidget()
        self.deep_search_table.setColumnCount(4)
        self.deep_search_table.setHorizontalHeaderLabels(["Search Time", "File Path", "Match Format", "Match Value"])
        self.deep_search_table.horizontalHeader().setStretchLastSection(True)
        self.deep_search_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.detail_tabs.addTab(self.deep_search_table, "Deep Search Results")
        
        splitter.addWidget(detail_widget)
        splitter.setSizes([300, 900])
        
        # 닫기 버튼
        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
    
    def load_results(self):
        """저장된 결과 목록 로드"""
        self.tree.clear()
        
        if not os.path.exists(self.results_dir):
            os.makedirs(self.results_dir, exist_ok=True)
            return
        
        # 파일명 기반으로 그룹화 (차수/모델명 추출)
        file_list = []
        for filename in os.listdir(self.results_dir):
            if not filename.endswith('.json'):
                continue
            
            filepath = os.path.join(self.results_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 사용자가 지정한 파일명 사용 (없으면 실제 파일명 사용)
                saved_filename = data.get('saved_filename', filename)
                display_name = saved_filename.replace('.json', '')
                
                # 파일명 파싱
                parts = display_name.split()
                order = '기타'
                manufacturer = ''
                model = ''
                scenario = ''
                
                if len(parts) >= 1:
                    # Check for order pattern: "N차" or "ExN" format
                    if '차' in parts[0] or (parts[0].startswith('Ex') and len(parts[0]) > 2 and parts[0][2:].isdigit()):
                        order = parts[0]
                        remaining = parts[1:] if len(parts) > 1 else []
                    else:
                        remaining = parts
                    
                    if len(remaining) >= 3:
                        manufacturer = remaining[0]
                        model = remaining[1]
                        scenario = ' '.join(remaining[2:])
                    elif len(remaining) == 2:
                        manufacturer = remaining[0]
                        model = remaining[1]
                    elif len(remaining) == 1:
                        model = remaining[0]
                
                file_list.append({
                    'filename': filename,
                    'filepath': filepath,
                    'data': data,
                    'display_name': display_name,
                    'order': order,
                    'manufacturer': manufacturer,
                    'model': model,
                    'scenario': scenario
                })
            except Exception as e:
                continue
        
        # 파일명에서 차수, 제조사, 모델명 추출하여 그룹화
        groups = {}
        for file_info in file_list:
            order = file_info.get('order', '기타')
            manufacturer = file_info.get('manufacturer', '')
            model = file_info.get('model', '')
            
            # 그룹화 키 생성 (차수 + 제조사 + 모델명, 시나리오명은 제외)
            if manufacturer:
                group_key = f"{order} {manufacturer} {model}".strip()
            else:
                group_key = f"{order} {model}".strip()
            
            if order not in groups:
                groups[order] = {}
            if group_key not in groups[order]:
                groups[order][group_key] = []
            
            groups[order][group_key].append(file_info)
        
        # 트리 구성
        for order in sorted(groups.keys()):
            order_item = QTreeWidgetItem(self.tree)
            order_item.setText(0, order)
            order_item.setExpanded(True)
            
            for group_key in sorted(groups[order].keys()):
                model_item = QTreeWidgetItem(order_item)
                model_item.setText(0, group_key)
                model_item.setExpanded(True)
                
                # 해당 그룹의 파일들
                for file_info in groups[order][group_key]:
                    result_item = QTreeWidgetItem(model_item)
                    result_item.setText(0, file_info['display_name'])
                    result_item.setData(0, Qt.UserRole, file_info['filepath'])
                    result_item.setData(0, Qt.UserRole + 1, file_info['data'])
    
    def on_selection_changed(self):
        """선택 변경 시 상세 정보 표시"""
        selected = self.tree.selectedItems()
        if not selected:
            return
        
        item = selected[0]
        filepath = item.data(0, Qt.UserRole)
        data = item.data(0, Qt.UserRole + 1)
        
        if not filepath or not data:
            return
        
        self.current_data = data
        self.current_filepath = filepath  # 파일 경로 저장 (저장 시 필요)
        self.display_result(data)
    
    def on_double_click(self, item, column):
        """더블 클릭 시 상세 정보 표시"""
        self.on_selection_changed()
    
    def display_result(self, data):
        """결과 상세 정보 표시"""
        # 기본 정보 (모델명, 초기화 시간, 메모 포함)
        timestamp = data.get('timestamp', 'N/A')
        try:
            if timestamp != 'N/A':
                dt = datetime.fromisoformat(timestamp)
                timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            pass
        
        confirmed_time = data.get('confirmed_time', 'N/A')
        model_name = data.get('model_name', 'N/A')
        memo = data.get('memo', '')
        source = str(data.get('source', '1'))
        file_path = str(data.get('file_path', ''))
        
        # 파일명에서 파싱된 정보 가져오기 (없으면 파일명에서 파싱)
        saved_filename = data.get('saved_filename', '')
        order = data.get('order', '')
        manufacturer = data.get('manufacturer', '')
        scenario = data.get('scenario', '')
        
        # 파일명에서 파싱 (저장된 값이 없으면)
        if not order or not manufacturer or not scenario:
            if saved_filename:
                parts = saved_filename.replace('.json', '').split()
                # Check for order pattern: "N차" or "ExN" format
                if len(parts) >= 1 and ('차' in parts[0] or (parts[0].startswith('Ex') and len(parts[0]) > 2 and parts[0][2:].isdigit())):
                    if not order:
                        order = parts[0]
                    if len(parts) >= 2 and not manufacturer:
                        manufacturer = parts[1]
                    if len(parts) >= 3 and (not model_name or model_name == 'N/A'):
                        model_name = parts[2]
                    if len(parts) >= 4 and not scenario:
                        scenario = ' '.join(parts[3:])
        
        # 편집 가능한 필드에 값 설정
        self.order_edit.setText(order if order else '')
        self.manufacturer_edit.setText(manufacturer if manufacturer else '')
        self.model_edit.setText(model_name if model_name != 'N/A' else '')
        self.scenario_edit.setText(scenario if scenario else '')
        self.confirmed_time_edit.setText(confirmed_time if confirmed_time != 'N/A' else '')
        self.memo_edit.setPlainText(memo)
        source_index = self.source_combo.findData(source)
        if source_index < 0:
            source_map = {"ZIP": "1", "ADB": "2", "Folder": "3"}
            source_index = self.source_combo.findData(source_map.get(source, "1"))
        self.source_combo.setCurrentIndex(source_index if source_index >= 0 else 0)
        self.original_path_edit.setText(file_path if file_path and file_path != "N/A" else "")
        
        # 읽기 전용 정보 표시
        info_text = f"""저장 시간: {timestamp}
원본 파일: {data.get('file_path', 'N/A')}
소스: {data.get('source', 'N/A')}
"""
        self.info_text.setPlainText(info_text)
        
        # 아티팩트 결과
        self.artifact_tabs.clear()
        artifact_names = {
            "1": "bootstat",
            "2-1": "recovery.log",
            "2-2": "last_log",
            "3": "suggestions.xml",
            "4": "persistent_properties",
            "5": "appops",
            "6": "wellbing",
            "7": "internal",
            "8": "eRR.p",
            "9": "ULR_PERSISTENT_PREFS.xml"
        }
        
        for artifact_id, artifact_data_list in data.get('artifact_data', {}).items():
            if not artifact_data_list:
                continue
            
            table = QTableWidget()
            table.setColumnCount(5)
            table.setHorizontalHeaderLabels(["아티팩트", "경로", "시간", "원본 시간", "메시지"])
            table.horizontalHeader().setStretchLastSection(True)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setAlternatingRowColors(True)
            
            for data_item in artifact_data_list:
                row = table.rowCount()
                table.insertRow(row)
                
                table.setItem(row, 0, QTableWidgetItem(data_item.get('name', '')))
                table.setItem(row, 1, QTableWidgetItem(data_item.get('path', '')))
                
                # 시간 표시
                if data_item.get('time'):
                    try:
                        dt = datetime.fromisoformat(data_item['time'])
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        if data_item.get('is_kst'):
                            time_str += " KST"
                        else:
                            time_str += " UTC"
                    except:
                        time_str = str(data_item.get('time', ''))
                else:
                    time_str = ""
                
                table.setItem(row, 2, QTableWidgetItem(time_str))
                table.setItem(row, 3, QTableWidgetItem(self._as_table_text(data_item.get('original_time', ''))))
                table.setItem(row, 4, QTableWidgetItem(data_item.get('message', '')))
            
            table.resizeColumnsToContents()
            artifact_name = artifact_names.get(artifact_id, f"아티팩트 {artifact_id}")
            self.artifact_tabs.addTab(table, artifact_name)
        
        # Summary results
        self.summary_table.setRowCount(0)
        all_times = []
        for artifact_id, artifact_data_list in data.get('artifact_data', {}).items():
            for data_item in artifact_data_list:
                if data_item.get('time'):
                    try:
                        dt = datetime.fromisoformat(data_item['time'])
                        all_times.append({
                            'time': dt,
                            'artifact_id': artifact_id,
                            'data': data_item
                        })
                    except:
                        pass
        
        all_times.sort(key=lambda x: x['time'])
        
        for item in all_times:
            row = self.summary_table.rowCount()
            self.summary_table.insertRow(row)
            
            artifact_name = artifact_names.get(item['artifact_id'], f"Artifact {item['artifact_id']}")
            data_item = item['data']
            
            self.summary_table.setItem(row, 0, QTableWidgetItem(artifact_name))
            self.summary_table.setItem(row, 1, QTableWidgetItem(data_item.get('path', '')))
            
            time_str = item['time'].strftime("%Y-%m-%d %H:%M:%S")
            if data_item.get('is_kst'):
                time_str += " KST"
            else:
                time_str += " UTC"
            
            self.summary_table.setItem(row, 2, QTableWidgetItem(time_str))
            self.summary_table.setItem(row, 3, QTableWidgetItem(self._as_table_text(data_item.get('original_time', ''))))
        
        self.summary_table.resizeColumnsToContents()
        
        # Deep search results
        self.deep_search_table.setRowCount(0)
        for result in data.get('deep_search_results', []):
            row = self.deep_search_table.rowCount()
            self.deep_search_table.insertRow(row)
            self.deep_search_table.setItem(row, 0, QTableWidgetItem(result.get('search_time', '')))
            self.deep_search_table.setItem(row, 1, QTableWidgetItem(result.get('file_path', '')))
            self.deep_search_table.setItem(row, 2, QTableWidgetItem(result.get('match_format', '')))
            self.deep_search_table.setItem(row, 3, QTableWidgetItem(result.get('match_value', '')))
        
        self.deep_search_table.resizeColumnsToContents()
    
    def save_edited_info(self):
        """편집된 상세 정보 저장"""
        try:
            if not self.current_data or not self.current_filepath:
                self.show_message("오류", "저장할 데이터가 선택되지 않았습니다.")
                return
            
            # 편집된 값 가져오기
            order = self.order_edit.text().strip()
            manufacturer = self.manufacturer_edit.text().strip()
            model_name = self.model_edit.text().strip()
            scenario = self.scenario_edit.text().strip()
            confirmed_time = self.confirmed_time_edit.text().strip()
            memo = self.memo_edit.toPlainText().strip()
            source = self.source_combo.currentData()
            original_path = self.original_path_edit.text().strip()
            
            # 데이터 업데이트
            self.current_data['order'] = order
            self.current_data['manufacturer'] = manufacturer
            self.current_data['model_name'] = model_name
            self.current_data['scenario'] = scenario
            self.current_data['confirmed_time'] = confirmed_time if confirmed_time else None
            self.current_data['memo'] = memo
            self.current_data['source'] = source if source else "1"
            self.current_data['file_path'] = original_path
            
            # 파일명도 업데이트 (차수, 제조사, 모델명, 시나리오명이 모두 있으면)
            if order and manufacturer and model_name and scenario:
                new_filename = f"{order} {manufacturer} {model_name} {scenario}.json"
                old_filename = os.path.basename(self.current_filepath)
                
                # 파일명이 변경되면 파일명도 변경
                if new_filename != old_filename:
                    new_filepath = os.path.join(self.results_dir, new_filename)
                    # 기존 파일이 있으면 덮어쓰기 확인
                    if os.path.exists(new_filepath) and new_filepath != self.current_filepath:
                        reply = QMessageBox.question(self, "파일 존재", 
                                                   f"'{new_filename}' 파일이 이미 존재합니다.\n덮어쓰시겠습니까?",
                                                   QMessageBox.Yes | QMessageBox.No)
                        if reply != QMessageBox.Yes:
                            return
                    
                    # 파일명 변경
                    try:
                        if os.path.exists(self.current_filepath):
                            os.rename(self.current_filepath, new_filepath)
                        self.current_filepath = new_filepath
                    except Exception as e:
                        self.show_message("경고", f"파일명 변경 실패: {str(e)}\n데이터는 저장되었습니다.")
                
                self.current_data['saved_filename'] = new_filename
            
            # JSON 직렬화 가능한 형태로 변환
            serializable_data = self._convert_to_json_serializable(self.current_data)
            
            # 파일 저장
            with open(self.current_filepath, 'w', encoding='utf-8') as f:
                json.dump(serializable_data, f, ensure_ascii=False, indent=2)
            
            # 성공 메시지
            self.show_message("성공", "상세 정보가 저장되었습니다.")
            
            # 목록 새로고침
            self.load_results()
            
        except Exception as e:
            import traceback
            error_msg = f"저장 중 오류가 발생했습니다: {str(e)}\n{traceback.format_exc()}"
            self.show_message("오류", error_msg)

    def browse_original_path(self):
        """원본 소스 타입에 따라 파일/폴더 경로 선택"""
        try:
            source = self.source_combo.currentData()
            current_path = self.original_path_edit.text().strip()
            start_dir = current_path if current_path and os.path.exists(current_path) else ""

            if source == "1":  # ZIP
                selected, _ = QFileDialog.getOpenFileName(
                    self,
                    "원본 ZIP 파일 선택",
                    start_dir,
                    "ZIP Files (*.zip);;All Files (*)",
                )
                if selected:
                    self.original_path_edit.setText(selected)
            elif source == "3":  # Folder
                selected = QFileDialog.getExistingDirectory(
                    self,
                    "원본 폴더 선택",
                    start_dir,
                )
                if selected:
                    self.original_path_edit.setText(selected)
            else:
                self.show_message("안내", "ADB 소스는 로컬 원본 파일 경로를 선택하지 않습니다.")
        except Exception as e:
            self.show_message("오류", f"경로 선택 중 오류: {e}")
    
    def _convert_to_json_serializable(self, obj):
        """datetime 객체를 JSON 직렬화 가능한 형태로 변환"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._convert_to_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_to_json_serializable(item) for item in obj]
        elif isinstance(obj, (int, float, str, bool, type(None))):
            return obj
        else:
            return str(obj)
    
    def show_message(self, title, message):
        """메시지 표시"""
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.exec_()
    
    def show_question(self, title, message):
        """질문 메시지 박스 표시"""
        reply = QMessageBox.question(self, title, message, 
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.No)
        return reply
    
    def delete_selected(self):
        """선택된 결과 삭제"""
        selected = self.tree.selectedItems()
        if not selected:
            self.show_message("경고", "삭제할 결과를 선택하세요.")
            return
        
        item = selected[0]
        filepath = item.data(0, Qt.UserRole)
        
        if not filepath:
            self.show_message("경고", "유효하지 않은 선택입니다.")
            return
        
        reply = self.show_question("확인", "선택한 결과를 삭제하시겠습니까?")
        if reply == QMessageBox.Yes:
            try:
                os.remove(filepath)
                self.load_results()  # 목록 새로고침
                self.show_message("완료", "삭제되었습니다.")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"삭제 중 오류가 발생했습니다:\n{e}")


if __name__ == "__main__":
    # Write a crash dump even for native (Qt) crashes
    try:
        import faulthandler
        from datetime import datetime as _dt
        _crash_path = os.path.join(os.path.dirname(__file__), "crash_dump.log")
        _CRASH_FH = open(_crash_path, "a", encoding="utf-8", buffering=1)
        _CRASH_FH.write("\n=== FactoryResetGUI start: " + _dt.now().isoformat() + " ===\n")
        _CRASH_FH.flush()
        faulthandler.enable(file=_CRASH_FH, all_threads=True)
    except Exception:
        try:
            import faulthandler
            faulthandler.enable(all_threads=True)
        except Exception:
            pass

    def qt_message_handler(mode, context, message):
        # QObject::connect 관련 경고 무시
        if "QObject::connect: Cannot queue arguments of type" in message:
            return
        if "QList<QPersistentModelIndex>" in message or "QVector<int>" in message or "QTextCursor" in message:
            return
        # 기본 동작 유지
        sys.stderr.write(message + "\n")

    qInstallMessageHandler(qt_message_handler)

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = FactoryResetGUI()
    window.show()
    sys.exit(app.exec_())
