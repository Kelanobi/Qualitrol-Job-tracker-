from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from app.auth import AuthenticationError


class LoginPage(QWidget):
    authenticated = Signal(object)

    def __init__(self, controller, assets_dir):
        super().__init__()
        self.controller = controller
        outer = QVBoxLayout(self)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        card = QFrame()
        card.setObjectName("loginCard")
        card.setMinimumWidth(460)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(42, 36, 42, 38)
        layout.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(QIcon(str(assets_dir / "qualitrol_logo.png")).pixmap(300, 75))
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)
        title = QLabel("DMS Order Tracker")
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        subtitle = QLabel("Sign in with your Qualitrol work account")
        subtitle.setObjectName("appSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addWidget(QLabel("Username or work email"))
        self.email = QLineEdit()
        self.email.setPlaceholderText("firstname.lastname or firstname.lastname@qualitrolcorp.com")
        self.email.setClearButtonEnabled(True)
        layout.addWidget(self.email)
        layout.addWidget(QLabel("Application password"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Enter password")
        layout.addWidget(self.password)
        self.show_password = QCheckBox("Show password")
        self.show_password.toggled.connect(
            lambda checked: self.password.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        layout.addWidget(self.show_password)
        self.error = QLabel("")
        self.error.setObjectName("loginError")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)
        self.sign_in = QPushButton("Sign In")
        self.sign_in.setObjectName("primaryButton")
        self.sign_in.clicked.connect(self.submit)
        self.email.returnPressed.connect(self.submit)
        self.password.returnPressed.connect(self.submit)
        layout.addWidget(self.sign_in)
        row.addWidget(card)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def reset(self) -> None:
        self.show_password.setChecked(False)
        self.email.clear()
        self.password.clear()
        try:
            username, password = self.controller.login_defaults()
        except Exception:
            username, password = "", ""
        self.email.setText(username)
        self.password.setText(password)
        self.error.clear()
        self.error.setVisible(False)
        if username and password:
            self.sign_in.setFocus()
        elif username:
            self.password.setFocus()
        else:
            self.email.setFocus()

    def submit(self) -> None:
        self.sign_in.setEnabled(False)
        self.error.setVisible(False)
        try:
            session = self.controller.authenticate(self.email.text(), self.password.text())
        except AuthenticationError as exc:
            self.password.clear()
            self.error.setText(str(exc))
            self.error.setVisible(True)
            self.password.setFocus()
        except Exception:
            self.password.clear()
            self.error.setText("Sign in could not be completed safely. Please try again.")
            self.error.setVisible(True)
        else:
            self.password.clear()
            self.authenticated.emit(session)
        finally:
            self.sign_in.setEnabled(True)
