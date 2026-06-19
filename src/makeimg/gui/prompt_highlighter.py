from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


class PromptHighlighter(QSyntaxHighlighter):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setForeground(QColor("#666666"))
        self._comment_fmt.setFontItalic(True)

        self._paren_fmt = QTextCharFormat()
        self._paren_fmt.setForeground(QColor("#4fc3f7"))

        self._weight_fmt = QTextCharFormat()
        self._weight_fmt.setForeground(QColor("#ffd54f"))

        self._bracket_fmt = QTextCharFormat()
        self._bracket_fmt.setForeground(QColor("#ffb74d"))

        self._brace_fmt = QTextCharFormat()
        self._brace_fmt.setForeground(QColor("#ce93d8"))

        self._pipe_fmt = QTextCharFormat()
        self._pipe_fmt.setForeground(QColor("#ff1744"))
        self._pipe_fmt.setFontWeight(QFont.Bold)

        self._comma_fmt = QTextCharFormat()
        self._comma_fmt.setForeground(QColor("#888888"))

        self._brace_paren_fmt = QTextCharFormat()
        self._brace_paren_fmt.setForeground(QColor("#00ff88"))
        self._brace_paren_fmt.setFontWeight(QFont.Bold)
        self._brace_paren_fmt.setFontItalic(True)
        self._brace_paren_fmt.setFontFamily("Consolas")

        self._brace_weight_fmt = QTextCharFormat()
        self._brace_weight_fmt.setForeground(QColor("#ff6600"))
        self._brace_weight_fmt.setFontWeight(QFont.Bold)
        self._brace_weight_fmt.setFontFamily("Consolas")

        self._comment_re = QRegularExpression(r"^\s*(#|//).*")
        self._paren_re = QRegularExpression(r"\([^)]*\)")
        self._bracket_re = QRegularExpression(r"\[[^\]]*\]")
        self._brace_re = QRegularExpression(r"\{[^}]*\}")

    def highlightBlock(self, text: str) -> None:
        text_len = len(text)

        match = self._comment_re.match(text)
        if match.hasMatch():
            self.setFormat(0, text_len, self._comment_fmt)
            return

        it = self._paren_re.globalMatch(text)
        while it.hasNext():
            m = it.next()
            start = m.capturedStart()
            length = min(m.capturedLength(), text_len - start)
            if start < 0 or length <= 0:
                continue
            self.setFormat(start, length, self._paren_fmt)
            inner = text[start:start + length]
            colon_pos = inner.find(":")
            if colon_pos >= 0:
                w_len = length - colon_pos
                if w_len > 0:
                    self.setFormat(start + colon_pos, w_len, self._weight_fmt)

        self._apply_regex(text, self._bracket_re, self._bracket_fmt)
        self._apply_brace_inner(text)

        for pos in range(text_len):
            if text[pos] == ',':
                self.setFormat(pos, 1, self._comma_fmt)

    def _apply_regex(self, text: str, regex: QRegularExpression, fmt: QTextCharFormat) -> None:
        text_len = len(text)
        it = regex.globalMatch(text)
        while it.hasNext():
            m = it.next()
            start = m.capturedStart()
            length = min(m.capturedLength(), text_len - start)
            if start < 0 or length <= 0:
                continue
            self.setFormat(start, length, fmt)

    def _apply_brace_inner(self, text: str) -> None:
        text_len = len(text)
        it = self._brace_re.globalMatch(text)
        while it.hasNext():
            m = it.next()
            start = m.capturedStart()
            length = min(m.capturedLength(), text_len - start)
            if start < 0 or length <= 0:
                continue
            self.setFormat(start, length, self._brace_fmt)
            end = min(start + length, text_len)
            inner_start = start + 1
            inner_end = end - 1
            inner_text = text[inner_start:inner_end] if inner_end > inner_start else ""
            offset = inner_start
            pi = 0
            paren_depth = 0
            paren_start = -1
            while pi < len(inner_text):
                ch = inner_text[pi]
                if ch == '(':
                    if paren_depth == 0:
                        paren_start = pi
                    paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                    if paren_depth == 0 and paren_start >= 0:
                        abs_s = offset + paren_start
                        abs_e = offset + pi + 1
                        self.setFormat(abs_s, abs_e - abs_s, self._brace_paren_fmt)
                        inner = inner_text[paren_start:pi + 1]
                        cp = inner.find(":")
                        if cp >= 0:
                            ws = abs_s + cp
                            wl = (abs_e - ws)
                            if wl > 0:
                                self.setFormat(ws, wl, self._brace_weight_fmt)
                        paren_start = -1
                elif ch == '|':
                    self.setFormat(offset + pi, 1, self._pipe_fmt)
                pi += 1