#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Shared, 80-column formatting for generated, human-readable output.

This is deliberately NOT a serializer: never reflow JSON/JSONL, patches, Git
commit messages, source snapshots, or the worksheet's parseable commit records.
"""
from __future__ import annotations

import re
import textwrap
from typing import Callable

WIDTH = 80
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FIELD = re.compile(r"^(\s*[^\s:][^:]{0,34}:\s+)")
_CONTROL = re.compile(r"^\s*(?:={40,}|-{40,})\s*$")
# Copyable locations in CLI output must remain a single logical line. Terminal
# soft-wrap is harmless; injecting real newlines into a pathname is not.
_LOCATION = re.compile(
    r"(?<!\S)(?:https?://\S+|(?:/|~/|\./|\.\./)\S+|"
    r"[^\s/]+/[^\s/]+/[^\s]+)"
)


def contains_copyable_location(line: str) -> bool:
    """Recognize URLs and absolute or multi-component relative paths."""
    return bool(_LOCATION.search(line))


def wrap_line(line: str, *, width: int = WIDTH) -> str:
    """Wrap one *display* line, preserving labels and hanging indentation.

    A single long URL/path may be broken for display only. The original value
    is never changed in a structured file or a Git command/message.
    """
    if len(_ANSI.sub('', line)) <= width or _CONTROL.fullmatch(line):
        return line
    if '\x1b' in line:  # Do not split escape sequences or bleed terminal color.
        return line
    space = re.match(r"^\s*", line).group(0)
    match = _FIELD.match(line)
    if match and len(match.group(1)) < width - 12:
        prefix = match.group(1)
        subsequent = ' ' * len(prefix)
        payload = line[len(prefix):]
    else:
        prefix = space
        subsequent = space + '  ' if len(space) < width - 12 else space
        payload = line[len(space):]
    if not payload:
        return line
    return '\n'.join(textwrap.wrap(
        payload, width=width, initial_indent=prefix,
        subsequent_indent=subsequent, break_long_words=True,
        break_on_hyphens=False, replace_whitespace=False,
        drop_whitespace=True) or [line])


def wrap_text(text: str, *, width: int = WIDTH,
              protected: Callable[[str], bool] | None = None) -> str:
    """Wrap generated prose without joining independent logical lines."""
    output = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        ending = line[len(body):]
        output.append((body if protected and protected(body) else
                       wrap_line(body, width=width)) + ending)
    return ''.join(output)


class ColumnWriter:
    """Line-buffered human output writer, preserving copyable locations.

    Ordinary console prose is wrapped to 80 columns. Lines containing paths
    or URLs are not forcibly wrapped, allowing the terminal to soft-wrap them
    instead. This also preserves paths in saved progress logs. Not used for
    JSON, patches, commit messages, or input data.
    """
    def __init__(self, stream, width: int = WIDTH):
        self.stream = stream
        self.width = width
        self.buffered = ''

    def write(self, data):
        if not isinstance(data, str):
            raise TypeError('text output requires a string')
        self.buffered += data
        while True:
            positions = [p for p in (self.buffered.find('\n'),
                                      self.buffered.find('\r')) if p != -1]
            if not positions:
                break
            at = min(positions)
            body, end = self.buffered[:at], self.buffered[at]
            displayed = (body if contains_copyable_location(body)
                         else wrap_line(body, width=self.width))
            self.stream.write(displayed + end)
            self.buffered = self.buffered[at + 1:]
        return len(data)

    def flush(self):
        if self.buffered:
            displayed = (self.buffered if contains_copyable_location(self.buffered)
                         else wrap_line(self.buffered, width=self.width))
            self.stream.write(displayed)
            self.buffered = ''
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    def __getattr__(self, key):
        return getattr(self.stream, key)
