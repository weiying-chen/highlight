#!/usr/bin/env python3
"""
Highlight source terms with *...* when they are used in timestamp content.

Rules:
- Only process source blocks under URL lines (until next blank line).
- Chinese term is highlighted if it appears in timestamp Chinese content
  (traditional/simplified tolerant).
- English term is highlighted if it appears in timestamp English content
  (case-insensitive).
- Timestamp format expected:
  [optional 'XXX ']HH:MM:SS:FF<TAB>HH:MM:SS:FF<TAB><Chinese text>
  followed by one English line.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TS_RE = re.compile(
    r"^(?:XXX\s+)?\d{2}:\d{2}:\d{2}:\d{2}\t\d{2}:\d{2}:\d{2}:\d{2}\t(.*)$"
)
URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Minimal mapping sufficient for terms used in this repo.
S2T = str.maketrans(
    {
        "湿": "濕",
        "热": "熱",
        "肾": "腎",
        "证": "證",
        "经": "經",
        "阴": "陰",
        "阳": "陽",
        "气": "氣",
        "医": "醫",
        "药": "藥",
        "疗": "療",
        "风": "風",
        "称": "稱",
        "满": "滿",
    }
)
T2S = str.maketrans(
    {
        v: k
        for k, v in {
            "湿": "濕",
            "热": "熱",
            "肾": "腎",
            "证": "證",
            "经": "經",
            "阴": "陰",
            "阳": "陽",
            "气": "氣",
            "医": "醫",
            "药": "藥",
            "疗": "療",
            "风": "風",
            "称": "稱",
            "满": "滿",
        }.items()
    }
)

ZH_LABEL_RE = re.compile(
    r"^(\s*(?:中文名|藥品名稱|药品名称|中文名稱|中文名称)(?:[\u4e00-\u9fff]{0,4})?[:：]\s*)(.+?)\s*$"
)
EN_LABEL_RE = re.compile(r"^(\s*(?:英文名|英文名稱|英文名称)[:：]\s*)(.+?)\s*$")
SHORT_BI_RE = re.compile(r"^[\u4e00-\u9fff]{2,10}\s+[A-Za-z][A-Za-z\- ]{2,120}$")
STANDALONE_EN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-' ]{2,120}$")
STANDALONE_ZH_RE = re.compile(r"^[\u4e00-\u9fff]{2,10}$")

# Candidate extractors for free-text source lines.
EN_TITLE_CHUNK_RE = re.compile(
    r"\b(?:[A-Z]\.|[A-Z][A-Za-z-]*)(?: (?:[A-Z]\.|[A-Z][A-Za-z-]*|and|of|the|for|in|with|to)){0,8}\b"
)
EN_TCM_LOWER_RE = re.compile(r"\b(?:[a-z]+(?:-[a-z]+)?(?: [a-z]+){0,6})\b")
ZH_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]{2,20}")

TCM_EN_KEYWORDS = {
    "damp",
    "phlegm",
    "heat",
    "fire",
    "deficiency",
    "disharmony",
    "stagnation",
    "obstruction",
    "qi",
    "yin",
    "yang",
    "spleen",
    "stomach",
    "liver",
    "kidney",
    "lung",
    "heart",
    "meridian",
    "channel",
    "syndrome",
    "tcm",
    "abdominal",
    "fullness",
    "acanthosis",
    "nigricans",
}
EN_CONNECTOR_STOPWORDS = {"and", "of", "the", "for", "in", "with", "to", "a", "an"}
HEADER_KEYS = {
    "TITLE",
    "URL",
    "SUMMARY",
    "YT_TITLE_SUGGESTED",
    "TITLE_SUGGESTED",
    "INTRO",
    "THUMBNAIL",
    "TIME_RANGE",
    "BODY",
}

# Restrict 2-char Chinese free-text matches to TCM-ish terms.
TCM_ZH_TWO_CHAR_RE = re.compile(
    r"^[心肝脾肺腎胃膽腸血氣陰陽痰濕風火熱寒瘀毒虛實經絡穴門病證症型滯鬱痿瘡斑痞滿]{2}$"
)
ZH_TERM_ALIASES = {
    "栝樓實": {"栝蔞實"},
    "栝蔞實": {"栝樓實"},
}


@dataclass
class MatchPools:
    zh_long: set[str]
    zh_short_tcm: set[str]
    en_title: set[str]
    en_tcm_lower: set[str]


def to_trad(text: str) -> str:
    return text.translate(S2T)


def to_simp(text: str) -> str:
    return text.translate(T2S)


def norm_en(text: str) -> str:
    text = text.replace("-", " ")
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text.strip()).lower()


def star_term(line: str, term: str) -> str:
    if not term:
        return line
    pattern = re.compile(rf"(?<!\*){re.escape(term)}")
    return pattern.sub(f"*{term}*", line)


def star_zh_term_variants(line: str, term: str) -> str:
    out = line
    variants = {term, to_simp(term), to_trad(term)}
    variants.update(ZH_TERM_ALIASES.get(term, set()))
    for v in list(variants):
        variants.add(to_simp(v))
        variants.add(to_trad(v))
    for v in sorted((x for x in variants if x), key=len, reverse=True):
        if v in out:
            out = star_term(out, v)
    return out


def star_en_phrase(line: str, phrase: str) -> str:
    toks = phrase.split()
    if not toks:
        return line
    token_boundary_left = r"(?<![A-Za-z0-9])"
    token_boundary_right = r"(?![A-Za-z0-9])"
    pattern = r"(?<!\*)" + token_boundary_left + re.escape(toks[0]) + token_boundary_right
    for tok in toks[1:]:
        pattern += (
            r"(?:[^A-Za-z0-9]+)"
            + token_boundary_left
            + re.escape(tok)
            + token_boundary_right
        )
    pattern += r"(?!\*)"
    return re.sub(pattern, lambda m: f"*{m.group(0)}*", line, flags=re.IGNORECASE)


def build_timestamp_corpora_from_lines(lines: list[str]) -> tuple[str, str]:
    zh_lines: list[str] = []
    en_lines: list[str] = []
    for i, line in enumerate(lines):
        m = TS_RE.match(line)
        if not m:
            continue
        zh = m.group(1).strip()
        if zh:
            zh_lines.append(zh)
        if i + 1 < len(lines):
            en = lines[i + 1].strip()
            if en and not URL_RE.match(en):
                en_lines.append(en)
    return "\n".join(zh_lines), "\n".join(en_lines)


def build_timestamp_corpora(paths: list[Path]) -> tuple[str, str]:
    zh_lines: list[str] = []
    en_lines: list[str] = []
    for p in paths:
        zh, en = build_timestamp_corpora_from_lines(
            p.read_text(encoding="utf-8").splitlines()
        )
        if zh:
            zh_lines.append(zh)
        if en:
            en_lines.append(en)
    return "\n".join(zh_lines), "\n".join(en_lines)


def _nearest_header(lines: list[str], start_idx: int) -> tuple[int, str] | None:
    for idx in range(start_idx, -1, -1):
        line = lines[idx]
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().upper()
        if key in HEADER_KEYS:
            return idx, key
    return None


def _split_nonempty_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
            continue
        if current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _looks_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _looks_english(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def _extract_intro_block_context(block: list[str]) -> tuple[str, str]:
    zh_lines: list[str] = []
    en_lines: list[str] = []

    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_chinese(stripped):
            zh_lines.append(stripped)
        if _looks_english(stripped):
            en_lines.append(stripped)

    return "\n".join(zh_lines), "\n".join(en_lines)


def build_intro_local_corpora(lines: list[str], url_idx: int) -> tuple[str, str]:
    header = _nearest_header(lines, url_idx - 1)
    if header is None:
        return "", ""

    header_idx, header_key = header
    if header_key != "INTRO":
        return "", ""

    blocks = _split_nonempty_blocks(lines[header_idx + 1 : url_idx])
    zh_block = ""
    en_block = ""

    for block in reversed(blocks):
        block_zh, block_en = _extract_intro_block_context(block)
        if not zh_block and block_zh:
            zh_block = block_zh
        if not en_block and block_en:
            en_block = block_en
        if zh_block and en_block:
            break

    return zh_block, en_block


def build_subtitle_local_corpora(
    lines: list[str], url_idx: int, max_pairs: int = 24
) -> tuple[str, str]:
    """Collect a local subtitle context immediately above a URL source block."""
    zh_lines: list[str] = []
    en_lines: list[str] = []
    i = url_idx - 1
    pairs = 0
    started = False

    while i >= 0 and pairs < max_pairs:
        line = lines[i]
        stripped = line.strip()
        m = TS_RE.match(line)
        if m:
            started = True
            zh = m.group(1).strip()
            if zh:
                zh_lines.append(zh)
            if i + 1 < len(lines):
                en = lines[i + 1].strip()
                if en and not URL_RE.match(en):
                    en_lines.append(en)
            pairs += 1
            i -= 1
            continue

        # Skip blank separators inside local subtitle regions.
        if stripped == "":
            if started:
                # Only the nearest contiguous subtitle block above the URL.
                break
            i -= 1
            continue

        if not started:
            # Walk upward through nearby non-empty lines (e.g., paired English
            # subtitle lines) until we reach the adjacent timestamp block.
            if URL_RE.match(stripped):
                break
            if ":" in stripped:
                key = stripped.split(":", 1)[0].strip().upper()
                if key in HEADER_KEYS:
                    break
            i -= 1
            continue

        # Once started, continue walking through paired English subtitle lines.
        if URL_RE.match(stripped):
            break
        if ":" in stripped:
            key = stripped.split(":", 1)[0].strip().upper()
            if key in HEADER_KEYS:
                break
        i -= 1
        continue

    zh_lines.reverse()
    en_lines.reverse()
    return "\n".join(zh_lines), "\n".join(en_lines)


def build_local_corpora(lines: list[str], url_idx: int, max_pairs: int = 24) -> tuple[str, str]:
    zh, en = build_intro_local_corpora(lines, url_idx)
    if zh or en:
        return zh, en
    return build_subtitle_local_corpora(lines, url_idx, max_pairs=max_pairs)


def has_zh(term: str, zh: str, zh_s: str, zh_t: str) -> bool:
    t = term.strip("* ").strip()
    if len(t) < 2:
        return False
    candidates = {t, to_simp(t), to_trad(t)}
    candidates.update(ZH_TERM_ALIASES.get(t, set()))
    for v in list(candidates):
        candidates.add(to_simp(v))
        candidates.add(to_trad(v))
    return any(v in zh or v in zh_s or v in zh_t for v in candidates if v)


def has_en(term: str, en_norm: str) -> bool:
    t = norm_en(term.replace("*", ""))
    if len(t) < 3:
        return False
    return t in en_norm


def best_en_partial(term: str, en_norm: str, min_words: int = 2) -> str:
    """Return the longest meaningful subphrase in term that appears in en corpus."""
    words = [w for w in norm_en(term).split() if w]
    if len(words) < min_words:
        return ""
    for n in range(len(words), min_words - 1, -1):
        for i in range(0, len(words) - n + 1):
            seg = words[i : i + n]
            if seg[0] in EN_CONNECTOR_STOPWORDS or seg[-1] in EN_CONNECTOR_STOPWORDS:
                continue
            cand = " ".join(seg)
            if cand in en_norm:
                return cand
    return ""


def likely_tcm_lower_phrase(phrase: str) -> bool:
    words = set(phrase.split())
    return any(w in TCM_EN_KEYWORDS for w in words)


def build_match_pools(zh_text: str, en_text: str) -> MatchPools:
    zh_long: set[str] = set()
    zh_short_tcm: set[str] = set()
    en_title: set[str] = set()
    en_tcm_lower: set[str] = set()

    for run in ZH_CHUNK_RE.findall(zh_text):
        if len(run) >= 3:
            zh_long.add(run)
            # Also collect 2-char TCM bigrams inside longer chunks (e.g., 痰瘀互結 -> 痰瘀).
            for i in range(len(run) - 1):
                bi = run[i : i + 2]
                if TCM_ZH_TWO_CHAR_RE.match(bi):
                    zh_short_tcm.add(bi)
        elif TCM_ZH_TWO_CHAR_RE.match(run):
            zh_short_tcm.add(run)

    for chunk in EN_TITLE_CHUNK_RE.findall(en_text):
        if len(chunk.split()) >= 2:
            en_title.add(chunk.strip())

    for chunk in EN_TCM_LOWER_RE.findall(en_text):
        c = chunk.strip()
        if len(c) >= 5 and likely_tcm_lower_phrase(c):
            en_tcm_lower.add(c)
            words = c.split()
            # Add compact keyword-centered windows (e.g., "abdominal fullness",
            # "acanthosis nigricans", "phlegm and blood stasis").
            for n in (2, 3, 4):
                if len(words) < n:
                    continue
                for i in range(len(words) - n + 1):
                    phrase_words = words[i : i + n]
                    if (
                        phrase_words[0] in EN_CONNECTOR_STOPWORDS
                        or phrase_words[-1] in EN_CONNECTOR_STOPWORDS
                    ):
                        continue
                    if any(w in TCM_EN_KEYWORDS for w in phrase_words):
                        en_tcm_lower.add(" ".join(phrase_words))

    return MatchPools(
        zh_long=zh_long,
        zh_short_tcm=zh_short_tcm,
        en_title=en_title,
        en_tcm_lower=en_tcm_lower,
    )


def process_source_line(
    line: str,
    zh: str,
    zh_s: str,
    zh_t: str,
    en_norm: str,
    pools: MatchPools,
) -> str:
    new = line

    m = ZH_LABEL_RE.match(new)
    if m:
        pre, value = m.groups()
        term = value.strip()
        if has_zh(term, zh, zh_s, zh_t):
            value = star_term(value, term)
        return pre + value

    m = EN_LABEL_RE.match(new)
    if m:
        pre, value = m.groups()
        parts = [
            x.strip()
            for x in re.split(r"[·|,;()（），、；：]+", value)
            if x.strip()
        ]
        for part in parts:
            if re.search(r"[A-Za-z]", part):
                if has_en(part, en_norm):
                    value = star_term(value, part)
                else:
                    partial = best_en_partial(part, en_norm)
                    if partial:
                        value = star_en_phrase(value, partial)
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", value):
            if has_zh(run, zh, zh_s, zh_t):
                value = star_term(value, run)
        return pre + value

    stripped = new.strip()

    if SHORT_BI_RE.match(stripped):
        zh_part = re.match(r"^([\u4e00-\u9fff]{2,10})\s+", stripped).group(1)
        en_part = re.search(r"([A-Za-z][A-Za-z\- ]{2,120})$", stripped).group(1).strip()
        if has_zh(zh_part, zh, zh_s, zh_t):
            new = star_term(new, zh_part)
        if has_en(en_part, en_norm):
            new = star_term(new, en_part)
        return new

    if STANDALONE_EN_RE.match(stripped):
        if has_en(stripped, en_norm):
            return star_term(new, stripped)
        return new

    if STANDALONE_ZH_RE.match(stripped):
        if has_zh(stripped, zh, zh_s, zh_t):
            return star_term(new, stripped)
        return new

    # Free-text lines under source blocks.
    for phrase in sorted(pools.en_title, key=len, reverse=True):
        if has_en(phrase, en_norm):
            new = star_en_phrase(new, phrase)

    for chunk in sorted(set(EN_TITLE_CHUNK_RE.findall(new)), key=len, reverse=True):
        phrase = chunk.strip()
        if len(phrase.split()) >= 2 and has_en(phrase, en_norm):
            new = star_en_phrase(new, phrase)

    for phrase in sorted(pools.en_tcm_lower, key=len, reverse=True):
        if has_en(phrase, en_norm):
            new = star_en_phrase(new, phrase)

    for run in sorted(set(ZH_CHUNK_RE.findall(new)), key=len, reverse=True):
        if has_zh(run, zh, zh_s, zh_t):
            new = star_zh_term_variants(new, run)

    for run in sorted(pools.zh_long, key=len, reverse=True):
        if has_zh(run, zh, zh_s, zh_t):
            new = star_zh_term_variants(new, run)

    for run in sorted(pools.zh_short_tcm, key=len, reverse=True):
        if has_zh(run, zh, zh_s, zh_t):
            new = star_zh_term_variants(new, run)

    return new


def transform_text(
    original: str, zh: str, zh_s: str, zh_t: str, en_norm: str, pools: MatchPools
) -> str:
    lines = original.splitlines()
    out: list[str] = []
    in_source = False
    local_ctx = (zh, zh_s, zh_t, en_norm, pools)

    for idx, line in enumerate(lines):
        stripped = line.strip()

        if URL_RE.match(stripped):
            # Build URL-local context from nearby subtitle block above this URL.
            l_zh, l_en = build_local_corpora(lines, idx)
            if l_zh or l_en:
                l_zh_s, l_zh_t = to_simp(l_zh), to_trad(l_zh)
                l_en_norm = norm_en(l_en)
                l_pools = build_match_pools(l_zh, l_en)
                local_ctx = (l_zh, l_zh_s, l_zh_t, l_en_norm, l_pools)
            else:
                local_ctx = (zh, zh_s, zh_t, en_norm, pools)
            in_source = True
            out.append(line)
            continue

        if in_source and stripped == "":
            in_source = False
            out.append(line)
            continue

        if not in_source:
            out.append(line)
            continue

        l_zh, l_zh_s, l_zh_t, l_en_norm, l_pools = local_ctx
        out.append(process_source_line(line, l_zh, l_zh_s, l_zh_t, l_en_norm, l_pools))

    return "\n".join(out) + "\n"


def process_file(
    path: Path, zh: str, zh_s: str, zh_t: str, en_norm: str, pools: MatchPools
) -> bool:
    original = path.read_text(encoding="utf-8")
    candidate = transform_text(original, zh, zh_s, zh_t, en_norm, pools)
    if candidate != original:
        path.write_text(candidate, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "files",
        nargs="*",
        help="Target files. Default: *_ch.txt in current directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: report files that would change, do not write.",
    )
    args = parser.parse_args()

    if args.files:
        paths = [Path(x) for x in args.files]
    else:
        paths = sorted(Path(".").glob("*_ch.txt"))

    if not paths:
        print("No target files found.", file=sys.stderr)
        return 1

    zh, en = build_timestamp_corpora(paths)
    zh_s, zh_t = to_simp(zh), to_trad(zh)
    en_norm = norm_en(en)
    pools = build_match_pools(zh, en)

    changed_files: list[Path] = []

    if args.check:
        for p in paths:
            original = p.read_text(encoding="utf-8")
            candidate = transform_text(original, zh, zh_s, zh_t, en_norm, pools)
            if candidate != original:
                changed_files.append(p)
        if changed_files:
            for p in changed_files:
                print(str(p))
            return 2
        return 0

    for p in paths:
        if process_file(p, zh, zh_s, zh_t, en_norm, pools):
            changed_files.append(p)

    for p in changed_files:
        print(str(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
