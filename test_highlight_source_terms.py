from __future__ import annotations

import unittest

import highlight_source_terms as subject


def _global_context(text: str) -> tuple[str, str, str, str, subject.MatchPools]:
    zh, en = subject.build_timestamp_corpora_from_lines(text.splitlines())
    zh_s, zh_t = subject.to_simp(zh), subject.to_trad(zh)
    en_norm = subject.norm_en(en)
    pools = subject.build_match_pools(zh, en)
    return zh, zh_s, zh_t, en_norm, pools


class HighlightSourceTermsTests(unittest.TestCase):
    def test_zh_label_line_highlights_matching_term(self) -> None:
        line = "中文名：腹脹"

        result = subject.process_source_line(
            line,
            "腹脹需要處理。",
            subject.to_simp("腹脹需要處理。"),
            subject.to_trad("腹脹需要處理。"),
            "",
            subject.build_match_pools("腹脹需要處理。", ""),
        )

        self.assertEqual("中文名：*腹脹*", result)

    def test_en_label_line_highlights_partial_phrase(self) -> None:
        line = "英文名: Severe abdominal fullness syndrome"
        en_text = "Abdominal fullness needs treatment."

        result = subject.process_source_line(
            line,
            "",
            "",
            "",
            subject.norm_en(en_text),
            subject.build_match_pools("", en_text),
        )

        self.assertEqual(
            "英文名: Severe *abdominal fullness* syndrome",
            result,
        )

    def test_short_bilingual_line_highlights_both_sides(self) -> None:
        line = "腹脹 Abdominal fullness"
        zh_text = "腹脹需要處理。"
        en_text = "Abdominal fullness needs treatment."

        result = subject.process_source_line(
            line,
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            subject.norm_en(en_text),
            subject.build_match_pools(zh_text, en_text),
        )

        self.assertEqual("*腹脹* *Abdominal fullness*", result)

    def test_standalone_english_line_highlights_match(self) -> None:
        result = subject.process_source_line(
            "Silent Spring",
            "",
            "",
            "",
            subject.norm_en("Silent Spring opens the story."),
            subject.build_match_pools("", "Silent Spring opens the story."),
        )

        self.assertEqual("*Silent Spring*", result)

    def test_standalone_chinese_line_highlights_variant_match(self) -> None:
        zh_text = "栝樓實需要炮製。"

        result = subject.process_source_line(
            "栝蔞實",
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            "",
            subject.build_match_pools(zh_text, ""),
        )

        self.assertEqual("*栝蔞實*", result)

    def test_free_text_source_line_highlights_title_and_chinese_chunks(self) -> None:
        zh_text = "春天故事慢慢展開。"
        en_text = "Silent Spring opens the story."

        result = subject.process_source_line(
            "Source note mentions Silent Spring and 春天故事.",
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            subject.norm_en(en_text),
            subject.build_match_pools(zh_text, en_text),
        )

        self.assertEqual(
            "Source note mentions *Silent Spring* and *春天故事*.",
            result,
        )

    def test_free_text_source_line_highlights_dotted_initial_name(self) -> None:
        zh_text = "測試"
        en_text = "As researcher J. Alex Carter said."

        result = subject.process_source_line(
            "Source note: J. Alex Carter.",
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            subject.norm_en(en_text),
            subject.build_match_pools(zh_text, en_text),
        )

        self.assertEqual(
            "Source note: *J. Alex Carter*.",
            result,
        )

    def test_free_text_source_line_highlights_name_inside_parentheses(self) -> None:
        zh_text = "示範練習"
        en_text = "As Professor Maya Lee said."

        result = subject.process_source_line(
            "他提到（Maya Lee）推廣此方法超過四十年。",
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            subject.norm_en(en_text),
            subject.build_match_pools(zh_text, en_text),
        )

        self.assertIn("*Maya Lee*", result)

    def test_en_label_with_fullwidth_punctuation_highlights_full_name(self) -> None:
        line = "英文名：卡特（J. Alex Carter，心理學研究者）"
        zh_text = "卡特"
        en_text = "As researcher J. Alex Carter said."

        result = subject.process_source_line(
            line,
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            subject.norm_en(en_text),
            subject.build_match_pools(zh_text, en_text),
        )

        self.assertIn("*J. Alex Carter*", result)

    def test_free_text_highlights_zh_before_starred_en_without_blocking(self) -> None:
        line = "這位學者林博士Iris Lin. 1980年生於某地"
        zh_text = "Lin( 林博士)提出了一個觀點"
        en_text = "Iris Lin once said."

        result = subject.process_source_line(
            line,
            zh_text,
            subject.to_simp(zh_text),
            subject.to_trad(zh_text),
            subject.norm_en(en_text),
            subject.build_match_pools(zh_text, en_text),
        )

        self.assertIn("*林博士*", result)
        self.assertIn("*Iris Lin*", result)

    def test_transform_text_only_processes_lines_inside_source_block(self) -> None:
        text = "\n".join(
            [
                "BODY:",
                "00:00:00:00\t00:00:02:00\t腹脹需要處理。",
                "Abdominal fullness needs treatment.",
                "",
                "https://example.com/source/body",
                "腹脹 Abdominal fullness",
                "",
                "This line mentions 腹脹 Abdominal fullness outside source block.",
                "",
            ]
        )

        zh, zh_s, zh_t, en_norm, pools = _global_context(text)
        result = subject.transform_text(text, zh, zh_s, zh_t, en_norm, pools)
        lines = result.splitlines()

        self.assertEqual("*腹脹* *Abdominal fullness*", lines[5])
        self.assertEqual(
            "This line mentions 腹脹 Abdominal fullness outside source block.",
            lines[7],
        )

    def test_transform_text_uses_intro_blocks_for_intro_source_url(self) -> None:
        text = "\n".join(
            [
                "INTRO:",
                "Silent Spring opens the story.",
                "春天故事慢慢展開。",
                "",
                "https://example.com/source/intro",
                "Source note mentions Silent Spring and 春天故事.",
                "",
                "BODY:",
                "00:00:00:00\t00:00:02:00\t完全無關的字幕。",
                "Completely unrelated subtitle.",
                "",
            ]
        )

        zh, zh_s, zh_t, en_norm, pools = _global_context(text)

        result = subject.transform_text(text, zh, zh_s, zh_t, en_norm, pools)

        self.assertIn(
            "Source note mentions *Silent Spring* and *春天故事*.",
            result,
        )

    def test_build_local_corpora_keeps_timestamp_context_for_body_source_url(self) -> None:
        lines = [
            "BODY:",
            "00:00:00:00\t00:00:02:00\t腹脹需要處理。",
            "Abdominal fullness needs treatment.",
            "",
            "https://example.com/source/body",
            "Source note.",
            "",
        ]

        zh, en = subject.build_local_corpora(lines, 4)

        self.assertEqual("腹脹需要處理。", zh)
        self.assertEqual("Abdominal fullness needs treatment.", en)


if __name__ == "__main__":
    unittest.main()
