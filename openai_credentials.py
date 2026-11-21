#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read OpenAI credentials without evaluating a shell script or printing secrets.

Environment variables take precedence. The shared fallback is deliberately a
small .env parser rather than `source`: reading a key must never run arbitrary
shell code. No network traffic or filesystem writes occur here.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

_KEY = re.compile(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*$")


def api_key_from_config(path: Path | None = None) -> str:
    """Return environment key, else ~/.config/kernel-backport/openai.env key.

    Supports the common KEY=value, export KEY=value and quoted KEY='value'
    forms. Does not expand shell variables or execute command substitutions.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    env_file = path if path is not None else Path.home() / ".config/kernel-backport/openai.env"
    try:
        contents = env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ValueError(f"cannot read OpenAI credentials file {env_file}: {exc.strerror}") from exc
    found = ""
    for line in contents.splitlines():
        match = _KEY.fullmatch(line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and
                           not value[end + 1:].strip().startswith("#")):
                raise ValueError(f"invalid OPENAI_API_KEY assignment in {env_file}")
            value = value[1:end]
        else:
            value = value.split(" #", 1)[0].strip()
        # Never execute shell constructs or substitute from the host environment.
        if "$" in value or "`" in value or "\x00" in value:
            raise ValueError(f"OPENAI_API_KEY in {env_file} must be a literal value")
        found = value
    return found
