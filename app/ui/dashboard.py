from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QStyle,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


class HeroBanner(QFrame):
    def __init__(self, image_path: Path):
        super().__init__()
        self._background = QPixmap(str(image_path))
        self.setObjectName("dashboardHero")
        self.setFixedHeight(132)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(42, 20, 22, 20)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        eyebrow = QLabel("DMS ORDER TRACKER")
        eyebrow.setObjectName("heroEyebrow")
        self.greeting = QLabel("Welcome back")
        self.greeting.setObjectName("heroGreeting")
        subtitle = QLabel("Verify DMS order documents and keep the workbook up to date.")
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setMinimumWidth(0)
        copy.addWidget(eyebrow)
        copy.addWidget(self.greeting)
        copy.addWidget(subtitle)
        layout.addLayout(copy)
        layout.addStretch()
        self.date_label = QLabel(datetime.now().strftime("%a, %d %b %Y"))
        self.date_label.setObjectName("heroDate")
        self.date_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.date_label, 0, Qt.AlignTop)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect(), 10, 10)
        painter.setClipPath(path)
        if not self._background.isNull():
            scaled = self._background.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = max(0, (scaled.width() - self.width()) // 2)
            y = max(0, (scaled.height() - self.height()) // 2)
            painter.drawPixmap(self.rect(), scaled, scaled.rect().adjusted(x, y, -x, -y))

    def set_user(self, email: str) -> None:
        local_name = email.split("@", 1)[0].split(".", 1)[0].strip()
        self.greeting.setText(f"Welcome back, {local_name.title()}" if local_name else "Welcome back")


class QuickActionCard(QFrame):
    clicked = Signal()

    def __init__(self, title: str, description: str, button_text: str, icon, primary: bool):
        super().__init__()
        self.setObjectName("quickActionCard")
        self.setFixedHeight(126)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setObjectName("quickActionIcon")
        icon_label.setPixmap(icon.pixmap(30, 30))
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(54, 54)
        heading.addWidget(icon_label)
        copy = QVBoxLayout()
        copy.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("quickActionTitle")
        description_label = QLabel(description)
        description_label.setObjectName("dashboardSubtitle")
        description_label.setWordWrap(True)
        copy.addWidget(title_label)
        copy.addWidget(description_label)
        heading.addLayout(copy, 1)
        layout.addLayout(heading)
        button = QPushButton(button_text)
        button.setObjectName("quickPrimary" if primary else "quickSecondary")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(self.clicked)
        layout.addWidget(button)


class MetricBlock(QWidget):
    selected = Signal(str)

    def __init__(self, key: str, title: str, color: str, show_progress: bool = True):
        super().__init__()
        self.key = key
        self.setObjectName("dashboardMetric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 6, 22, 8)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet(
            "QProgressBar{background:#e5eaf0;border:none;border-radius:3px;}"
            f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
        )
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("metricPercent")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.percent_label)
        layout.addLayout(progress_row)
        if not show_progress:
            self.progress.hide()
            self.percent_label.hide()

    def set_metric(self, value: int, total: int) -> None:
        self.value_label.setText(str(value))
        percentage = round((value / total) * 100) if total else 0
        self.progress.setValue(percentage)
        self.percent_label.setText(f"{percentage}%")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.key)
        super().mouseReleaseEvent(event)


class DashboardPage(QWidget):
    automatic_requested = Signal()
    automatic_recheck_requested = Signal()
    manual_requested = Signal()
    history_requested = Signal(str)
    reset_requested = Signal()
    report_requested = Signal()
    detail_requested = Signal(str)

    def __init__(self, assets_dir: Path | None = None):
        super().__init__()
        assets_dir = Path(assets_dir or "assets")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 8, 6, 12)
        outer.setSpacing(12)

        self.hero = HeroBanner(assets_dir / "transformer_banner_2026-10-22.png")
        outer.addWidget(self.hero)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(12)
        # Start compact so the horizontal minimum-size hint cannot force a
        # narrow maximized window beyond the physical screen before its first
        # resize event arrives.
        self.actions_layout.setDirection(QBoxLayout.TopToBottom)
        automatic = QuickActionCard(
            "Automatic Search", "Find and verify DMS orders that have never been checked before.",
            "Start Automatic Search  ›", self.style().standardIcon(QStyle.SP_FileDialogContentsView), True,
        )
        manual = QuickActionCard(
            "Manual Search", "Enter specific DMS order numbers to verify.",
            "Start Manual Search  ›", self.style().standardIcon(QStyle.SP_FileIcon), False,
        )
        self.automatic_recheck = QuickActionCard(
            "Automatic Recheck All", "Recheck every DMS order in the current workbook.",
            "Recheck All Orders  ›", self.style().standardIcon(QStyle.SP_BrowserReload), False,
        )
        automatic.clicked.connect(self.automatic_requested)
        manual.clicked.connect(self.manual_requested)
        self.automatic_recheck.clicked.connect(self.automatic_recheck_requested)
        self.actions_layout.addWidget(automatic, 1)
        self.actions_layout.addWidget(manual, 1)
        self.actions_layout.addWidget(self.automatic_recheck, 1)
        outer.addLayout(self.actions_layout)

        summary = QFrame()
        summary.setObjectName("dashboardPanel")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        summary_layout.setSpacing(12)
        summary_header = QHBoxLayout()
        summary_copy = QVBoxLayout()
        summary_copy.setSpacing(1)
        summary_title = QLabel("Verification Summary")
        summary_title.setObjectName("dashboardTitle")
        summary_subtitle = QLabel("Overview of DMS orders in the current workbook.")
        summary_subtitle.setObjectName("dashboardSubtitle")
        summary_subtitle.setWordWrap(True)
        summary_subtitle.setMinimumWidth(0)
        summary_copy.addWidget(summary_title)
        summary_copy.addWidget(summary_subtitle)
        summary_header.addLayout(summary_copy)
        summary_header.addStretch()
        self.report_button = QPushButton("Download Monthly Report")
        self.report_button.setObjectName("dashboardRefresh")
        self.report_button.clicked.connect(self.report_requested)
        summary_header.addWidget(self.report_button)
        self.refresh_button = QPushButton("Reset Month")
        self.refresh_button.setObjectName("dashboardRefresh")
        self.refresh_button.setToolTip("Start this month's dashboard totals again from zero")
        self.refresh_button.clicked.connect(self.reset_requested)
        summary_header.addWidget(self.refresh_button)
        summary_layout.addLayout(summary_header)

        metrics = QHBoxLayout()
        metrics.setSpacing(0)
        self.total_metric = MetricBlock("all", "Total Orders", "#4f6f94", False)
        self.verified_metric = MetricBlock("Completed Pass", "Completed Pass", "#18a957")
        self.missing_metric = MetricBlock("Missing Documents", "Missing Documents", "#f0a000")
        self.cards = {
            "all": self.total_metric,
            "Completed Pass": self.verified_metric,
            "Missing Documents": self.missing_metric,
        }
        for index, metric in enumerate(self.cards.values()):
            metric.selected.connect(self.history_requested)
            metrics.addWidget(metric, 1)
            if index < 2:
                divider = QFrame()
                divider.setObjectName("metricDivider")
                divider.setFrameShape(QFrame.VLine)
                metrics.addWidget(divider)
        summary_layout.addLayout(metrics)
        outer.addWidget(summary)

        recent = QFrame()
        recent.setObjectName("dashboardPanel")
        recent_layout = QVBoxLayout(recent)
        recent_layout.setContentsMargins(18, 14, 18, 16)
        recent_layout.setSpacing(10)
        recent_header = QHBoxLayout()
        marker = QFrame()
        marker.setObjectName("sectionMarker")
        marker.setFixedSize(4, 36)
        recent_header.addWidget(marker)
        recent_copy = QVBoxLayout()
        recent_copy.setSpacing(1)
        recent_title = QLabel("Recent Results")
        recent_title.setObjectName("dashboardTitle")
        recent_subtitle = QLabel("Latest verification results from the most recent searches.")
        recent_subtitle.setObjectName("dashboardSubtitle")
        recent_subtitle.setWordWrap(True)
        recent_subtitle.setMinimumWidth(0)
        recent_copy.addWidget(recent_title)
        recent_copy.addWidget(recent_subtitle)
        recent_header.addLayout(recent_copy)
        recent_header.addStretch()
        view_all = QPushButton("View All in History  ›")
        view_all.setObjectName("historyLink")
        view_all.clicked.connect(lambda: self.history_requested.emit(""))
        recent_header.addWidget(view_all)
        recent_layout.addLayout(recent_header)

        self.table = QTableWidget(0, 9)
        self.table.setObjectName("recentResultsTable")
        self.table.setHorizontalHeaderLabels([
            "Order Number", "Order Type", "Specs", "QA", "DCL", "Drawings", "FAT", "Status", "Last Checked",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(
            lambda row, _column: self.detail_requested.emit(self.table.item(row, 0).text())
        )
        self.table.setAlternatingRowColors(False)
        self.table.setMinimumHeight(190)
        recent_layout.addWidget(self.table, 1)
        outer.addWidget(recent)

        self.updated_label = QLabel("Not yet updated")
        self.updated_label.setObjectName("dashboardUpdated")
        self.updated_label.setAlignment(Qt.AlignRight)
        self.updated_label.hide()
        outer.addWidget(self.updated_label)

    def resizeEvent(self, event) -> None:
        # Windows display scaling can reduce a 14-inch 1080p screen to fewer
        # than 1,000 logical pixels. Stack the action cards instead of letting
        # their contents run beyond the viewport.
        direction = QBoxLayout.TopToBottom if event.size().width() < 1050 else QBoxLayout.LeftToRight
        if self.actions_layout.direction() != direction:
            self.actions_layout.setDirection(direction)
        super().resizeEvent(event)

    def set_user(self, email: str) -> None:
        self.hero.set_user(email)

    def set_data(self, data: dict[str, object], updated: str) -> None:
        total = int(data["total"])
        verified = int(data["completed"])
        missing = int(data["missing"])
        self.total_metric.set_metric(total, total)
        self.verified_metric.set_metric(verified, total)
        self.missing_metric.set_metric(missing, total)
        self.updated_label.setText(f"Last updated: {updated}")
        self.updated_label.hide()
        self.refresh_button.setEnabled(True)

        rows = data.get("recent", [])
        self.table.setRowCount(len(rows))
        positive_icon = self.style().standardIcon(QStyle.SP_DialogApplyButton)
        missing_icon = self.style().standardIcon(QStyle.SP_DialogCancelButton)
        neutral_icon = self.style().standardIcon(QStyle.SP_MessageBoxInformation)
        for row_index, row in enumerate(rows):
            checked = str(row["completed_at"]).replace("T", " ")
            values = [
                row["order_number"], row["order_category"], row["specs"], row["qa"],
                row["dcl"], row["drawings"], row["fat"], row["overall_status"], checked,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (2, 3, 4, 5, 6):
                    text = str(value).strip().lower()
                    item.setIcon(positive_icon if text in {"yes", "present"} else neutral_icon if text in {"n/a", "na"} else missing_icon)
                if column == 7:
                    if str(value) == "Completed Pass":
                        item.setForeground(QColor("#17894d"))
                    else:
                        item.setForeground(QColor("#d65f00"))
                self.table.setItem(row_index, column, item)

    def set_loading(self) -> None:
        self.updated_label.setText("Refreshing workbook data…")
        self.updated_label.show()
        self.refresh_button.setEnabled(False)

    def set_unavailable(self, message: str) -> None:
        for metric in self.cards.values():
            metric.value_label.setText("—")
            metric.progress.setValue(0)
            metric.percent_label.setText("0%")
        self.updated_label.setText(message)
        self.updated_label.show()
        self.refresh_button.setEnabled(True)
        self.table.setRowCount(0)
