"""Helper functions used throughout the bot."""

import re
import os
from typing import List
from difflib import SequenceMatcher
import httpx


def sanitize_filename(filename: str) -> str:
    """Sanitizes a string to be a valid filename."""
    filename = str(filename)
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename).strip(". ")
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[: 200 - len(ext)] + ext
    return filename


def split_message(text: str, max_length: int = 1900) -> List[str]:
    """Splits a string into chunks respecting lines and words."""
    if len(text) <= max_length:
        return [text]

    lines = text.split("\n")
    chunks = []
    current_chunk = ""

    for line in lines:
        while len(line) > max_length:
            split_pos = line.rfind(" ", 0, max_length)
            if split_pos == -1:
                split_pos = max_length

            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""

            chunks.append(line[:split_pos])
            line = line[split_pos:].lstrip()

        if len(current_chunk) + len(line) + 1 > max_length:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def calculate_edit_percentage(old_text: str, new_text: str) -> float:
    """Calculates the percentage difference between two texts."""
    if not old_text and not new_text:
        return 0.0
    if not old_text or not new_text:
        return 100.0
    return (1 - SequenceMatcher(None, old_text, new_text).ratio()) * 100


def is_likely_typo(original: str, edited: str) -> bool:
    """
    Determines if an edit is likely a typo fix vs intentional content change.
    Returns True if it's probably a typo, False if it's content manipulation.
    """
    if not original or not edited:
        return False

    original_words = original.split()
    edited_words = edited.split()

    word_count_diff = abs(len(original_words) - len(edited_words))
    if word_count_diff > 1:
        return False

    matcher = SequenceMatcher(None, original_words, edited_words)
    changed_words = 0
    completely_different_words = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            for orig_word, edit_word in zip(
                original_words[i1:i2], edited_words[j1:j2]
            ):
                word_similarity = SequenceMatcher(
                    None, orig_word.lower(), edit_word.lower()
                ).ratio()
                changed_words += 1
                if word_similarity < 0.6:
                    completely_different_words += 1
        elif tag in ("delete", "insert"):
            changed_words += abs(i2 - i1) + abs(j2 - j1)

    if completely_different_words > 0:
        return False

    return changed_words <= 2


def format_alt_name(username: str) -> str:
    """Formats a raw username for safe display and makes it clickable."""
    display_name = username
    search_name = username

    if username.startswith("..."):
        display_name = username[3:]
        search_name = display_name
    elif username.startswith(".ASW"):
        display_name = f"{username[1:]} (Web)"
        search_name = username[1:]
    elif username.startswith("."):
        display_name = f"{username[1:]} (Cracked)"
        search_name = username[1:]

    encoded_name = httpx.URL(f"https://namemc.com/search?q={search_name}").__str__()
    return f"[{display_name}](<{encoded_name}>)"


def format_alts_grid(alts: List[str], max_per_line: int = 3) -> List[str]:
    """Formats a list of alts into a grid pattern."""
    lines = []
    for i in range(0, len(alts), max_per_line):
        chunk = alts[i:i + max_per_line]
        lines.append(", ".join(chunk))
    return lines


def is_valid_ipv4(ip: str) -> bool:
    """Validates IPv4 address format."""
    return bool(re.match(r"^(?:\d{1,3}\.){3}\d{1,3}$", ip))


def is_valid_ipv6(ip: str) -> bool:
    """Validates IPv6 address format (both compressed and uncompressed)."""
    # IPv6 pattern supporting both compressed (::) and uncompressed forms
    ipv6_pattern = re.compile(
        r"^("
        r"([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|"  # Full form
        r"([0-9a-fA-F]{1,4}:){1,7}:|"  # Compressed with :: at end
        r"([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|"  # Compressed
        r"([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|"
        r"([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|"
        r"([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|"
        r"([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|"
        r"[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|"
        r":((:[0-9a-fA-F]{1,4}){1,7}|:)|"  # :: at beginning
        r"fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|"  # Link-local
        r"::(ffff(:0{1,4}){0,1}:){0,1}"
        r"((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3}"
        r"(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|"  # IPv4-mapped
        r"([0-9a-fA-F]{1,4}:){1,4}:"
        r"((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3}"
        r"(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])"  # IPv4-embedded
        r")$"
    )
    return bool(ipv6_pattern.match(ip))


def is_valid_ip(ip: str) -> bool:
    """Validates both IPv4 and IPv6 addresses."""
    return is_valid_ipv4(ip) or is_valid_ipv6(ip)
