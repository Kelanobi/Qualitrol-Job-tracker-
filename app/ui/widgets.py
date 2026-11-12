from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class StatusDot(QWidget):
    def __init__(self, color: str = "#2eaa50", parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(14, 14)

    def set_color(self, color: str) -> None:
        self.color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(1, 1, 12, 12)


class SummaryCard(QPushButton):
    selected = Signal(str)

    def __init__(self, key: str, title: str, value: str, accent: str, icon, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("summaryCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(80)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        icon_box = QLabel()
        icon_box.setPixmap(icon.pixmap(36, 36))
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setFixedSize(58, 58)
        icon_box.setStyleSheet(f"background:{accent}; border-radius:12px;")
        layout.addWidget(icon_box)
        text = QVBoxLayout()
        value_label = QLabel(value)
        value_label.setObjectName("summaryValue")
        title_label = QLabel(title)
        title_label.setObjectName("summaryTitle")
        text.addWidget(value_label)
        text.addWidget(title_label)
        layout.addLayout(text, 1)
        arrow = QLabel("›")
        arrow.setObjectName("cardArrow")
        layout.addWidget(arrow)
        self.value_label = value_label
        self.clicked.connect(lambda: self.selected.emit(self.key))


class Section(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("section")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 18, 20, 18)
        self.layout.setSpacing(12)
        if title:
            header = QHBoxLayout()
            marker = QFrame()
            marker.setObjectName("sectionMarker")
            marker.setFixedSize(4, 24)
            header.addWidget(marker)
            labels = QVBoxLayout()
            labels.setSpacing(1)
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            labels.addWidget(title_label)
            if subtitle:
                subtitle_label = QLabel(subtitle)
                subtitle_label.setObjectName("muted")
                labels.addWidget(subtitle_label)
            header.addLayout(labels)
            header.addStretch()
            self.layout.addLayout(header)

