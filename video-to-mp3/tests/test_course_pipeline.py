"""
Unit tests for course pipeline features in server.py
"""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from http.server import ThreadingHTTPServer
import threading

import server


class TestCoursePipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sanitize_course_name(self):
        dirty = '課程: "Python" / <AI> *測試* ?'
        clean = server.sanitize_course_name(dirty)
        self.assertEqual(clean, "課程 Python AI 測試")

    def test_derive_course_name_from_local_file_and_folder(self):
        self.assertEqual(
            server.derive_course_name("local_video", r"D:\課程\Python 入門.mp4"),
            "Python 入門",
        )
        self.assertEqual(
            server.derive_course_name("local_mp3", r"D:\課程\第一章.mp3"),
            "第一章",
        )
        self.assertEqual(
            server.derive_course_name("mp3_folder", r"D:\課程\完整課程"),
            "完整課程",
        )

    def test_derive_course_name_from_youtube_title(self):
        title = server.derive_course_name(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            youtube_title_loader=lambda _url: 'Python: "AI" / 入門',
        )
        self.assertEqual(title, "Python AI 入門")

    def test_derive_course_name_rejects_empty_title(self):
        with self.assertRaisesRegex(ValueError, "無法取得課程名稱"):
            server.derive_course_name(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                youtube_title_loader=lambda _url: "",
            )

    def test_valid_youtube_manifest_creation(self):
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        valid = server.validate_course_source("youtube", yt_url)
        self.assertTrue(valid)

        manifest, manifest_path = server.create_course_manifest(
            source_type="youtube",
            source_val=yt_url,
            course_name="YT測試課程",
            options={"skillTreeMode": 2},
            output_root=self.output_root
        )
        self.assertTrue(manifest_path.exists())
        self.assertEqual(manifest["courseName"], "YT測試課程")
        self.assertEqual(manifest["source"]["type"], "youtube")
        self.assertEqual(manifest["options"]["skillTreeMode"], 2)

    def test_local_video_manifest_has_source_aware_criteria(self):
        source = Path(self.output_root) / "lesson.mp4"
        source.write_bytes(b"not-empty")
        out = Path(self.output_root) / "out"
        manifest, _ = server.create_course_manifest(
            "local_video", str(source), "本機錄影", output_root=str(out)
        )
        self.assertNotIn(
            "YouTube", manifest["stages"]["acquisition"]["completion_criteria"]
        )
        self.assertEqual(manifest["options"]["youtubeArtifacts"], [])

    def test_invalid_local_source_rejection(self):
        fake_path = r"C:\non_existent_folder_xyz\fake.mp4"
        valid = server.validate_course_source("local_video", fake_path)
        self.assertFalse(valid)

    def test_deceptive_youtube_hostname_rejection(self):
        self.assertFalse(server.validate_course_source(
            "youtube", "https://notyoutube.com/watch?v=bad"
        ))

    def test_invalid_skill_mode_leaves_no_orphan_directory(self):
        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                "非法模式",
                options={"skillTreeMode": 9},
                output_root=self.output_root,
            )
        self.assertEqual(list(Path(self.output_root).iterdir()), [])

    def test_default_artifacts_match_legacy_behaviour(self):
        """不指定 artifacts 時，維持原本五份課程包全開、技能樹不做。"""
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "預設值課程",
            output_root=self.output_root,
        )
        self.assertEqual(
            manifest["options"]["artifacts"],
            {
                "video": True,          # YouTube 來源預設保留 MP4
                "mp3": True,
                "transcript": True,
                "review": False,
                "rawSegments": False,
                "summary": True,
                "report": True,
                "mindmap": True,
                "skillTree": False,
            },
        )
        self.assertEqual(manifest["stages"]["transcript_review"]["status"], "skipped")
        self.assertEqual(manifest["stages"]["skill_tree"]["status"], "skipped")

    def test_artifact_selection_marks_unchosen_stages_skipped(self):
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "只要摘要",
            options={"artifacts": {"summary": True, "report": False, "mindmap": False}},
            output_root=self.output_root,
        )
        stages = manifest["stages"]
        self.assertEqual(stages["summary"]["status"], "pending")
        self.assertEqual(stages["training_report"]["status"], "skipped")
        self.assertEqual(stages["mindmap"]["status"], "skipped")
        # 下游只需要逐字稿；轉錄器可直接吃原始媒體，不需要先產永久 MP3。
        self.assertEqual(stages["transcription"]["status"], "pending")
        self.assertEqual(stages["media_to_mp3"]["status"], "skipped")
        self.assertTrue(manifest["options"]["artifacts"]["transcript"])
        self.assertFalse(manifest["options"]["artifacts"]["mp3"])

    def test_transcript_only_does_not_enable_mp3_artifact(self):
        """逐字稿可直接讀媒體；不能再把永久 MP3 當成強制前置。"""
        artifacts = {key: False for key in server.ARTIFACT_KEYS}
        artifacts["transcript"] = True
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "只做逐字稿",
            options={"artifacts": artifacts},
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["artifacts"]["transcript"])
        self.assertFalse(manifest["options"]["artifacts"]["mp3"])
        self.assertEqual(manifest["stages"]["transcription"]["status"], "pending")
        self.assertEqual(manifest["stages"]["media_to_mp3"]["status"], "skipped")

    def test_dependency_autofill_from_any_downstream_artifact(self):
        for downstream in ("summary", "report", "mindmap", "skillTree", "review"):
            artifacts = {k: False for k in server.ARTIFACT_KEYS}
            artifacts[downstream] = True
            options = {"artifacts": artifacts}
            if downstream == "skillTree":
                options["skillTreeMode"] = 1
            manifest, _ = server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                f"相依{downstream}",
                options=options,
                output_root=self.output_root,
            )
            with self.subTest(downstream=downstream):
                self.assertTrue(manifest["options"]["artifacts"]["transcript"])
                self.assertFalse(manifest["options"]["artifacts"]["mp3"])
                self.assertEqual(manifest["stages"]["transcription"]["status"], "pending")
                self.assertEqual(manifest["stages"]["media_to_mp3"]["status"], "skipped")

    def test_mp3_source_skips_conversion_stage(self):
        mp3_path = Path(self.temp_dir.name) / "來源.mp3"
        mp3_path.write_bytes(b"fake mp3")
        manifest, _ = server.create_course_manifest(
            "local_mp3",
            str(mp3_path),
            "來源已是MP3",
            output_root=self.output_root,
        )
        self.assertFalse(manifest["options"]["artifacts"]["mp3"])
        self.assertEqual(manifest["stages"]["media_to_mp3"]["status"], "skipped")
        self.assertEqual(manifest["stages"]["transcription"]["status"], "pending")

    def test_rejects_manifest_with_no_artifact_selected(self):
        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                "全部沒勾",
                options={"artifacts": {k: False for k in server.ARTIFACT_KEYS}},
                output_root=self.output_root,
            )

    def test_skill_tree_artifact_and_mode_stay_consistent(self):
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "技能樹模式二",
            options={"artifacts": {"skillTree": True}, "skillTreeMode": 2},
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["artifacts"]["skillTree"])
        self.assertEqual(manifest["options"]["skillTreeMode"], 2)
        self.assertEqual(manifest["stages"]["skill_tree"]["status"], "pending")

        # 勾了技能樹卻給 mode 0 是矛盾組合
        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                "技能樹矛盾",
                options={"artifacts": {"skillTree": True}, "skillTreeMode": 0},
                output_root=self.output_root,
            )

    def test_mode3_invariants(self):
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "模式三",
            options={
                "skillTreeMode": 3,
                "minimumExample": False,
                "youtubeArtifacts": ["mp3"],
            },
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["minimumExample"])
        self.assertEqual(
            manifest["options"]["youtubeArtifacts"],
            ["mp4", "mp3", "metadata", "subtitle_or_raw_transcript"],
        )

    def test_non_overwriting_directory_suffix(self):
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        m1, p1 = server.create_course_manifest("youtube", yt_url, "同名課程", output_root=self.output_root)
        m2, p2 = server.create_course_manifest("youtube", yt_url, "同名課程", output_root=self.output_root)

        self.assertNotEqual(p1.parent, p2.parent)
        self.assertTrue(str(p2.parent).endswith("-2"))

    def test_manifest_schema_and_enums(self):
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        manifest, _ = server.create_course_manifest("youtube", yt_url, "Schema測試", output_root=self.output_root)
        
        self.assertEqual(manifest["schemaVersion"], "1.1")
        expected_stages = [
            "acquisition", "media_to_mp3", "transcription", "transcript_review",
            "raw_segments", "summary", "training_report", "mindmap",
            "skill_tree", "archive",
        ]
        self.assertListEqual(list(manifest["stages"].keys()), expected_stages)
        for name, stage in manifest["stages"].items():
            expected = ("skipped"
                        if name in {"skill_tree", "transcript_review", "raw_segments"}
                        else "pending")
            self.assertEqual(stage["status"], expected)
            self.assertIn("completion_criteria", stage)
            self.assertIn("evidence", stage)
            self.assertIn("outputs", stage)
            self.assertIn("error", stage)

    def test_course_page_contract(self):
        self.assertIn('data-mode="course"', server.PAGE)
        self.assertIn('id="courseCard"', server.PAGE)
        self.assertIn("/api/course/create", server.PAGE)
        self.assertIn("#course", server.PAGE)

    def test_course_page_has_pickers_and_automatic_name(self):
        for token in (
            'id="courseSourcePick"',
            'id="courseOutPick"',
            "/api/course/pick",
            "/api/course/name",
            "pickCourseSource()",
            "pickCourseOutput()",
            "suggestCourseName()",
            "courseSourceTypeChanged()",
        ):
            self.assertIn(token, server.PAGE)

    def test_course_page_explains_and_copies_ai_handoff(self):
        for token in (
            'id="courseCopyPrompt"',
            "copyCoursePrompt()",
            "複製 AI 執行指令",
            "回到目前的 Hermes 對話貼上",
            "執行 course-content-pipeline",
        ):
            self.assertIn(token, server.PAGE)

    def _mp3_folder(self, count: int = 3) -> str:
        folder = Path(self.temp_dir.name) / "20260818_八小時課程"
        folder.mkdir()
        for i in range(1, count + 1):
            (folder / f"part{i}.mp3").write_bytes(b"fake mp3")
        return str(folder)

    def test_multi_part_source_is_accepted_and_named_by_folder(self):
        folder = self._mp3_folder()
        self.assertTrue(server.validate_course_source("mp3_parts", folder))
        self.assertEqual(
            server.derive_course_name("mp3_parts", folder), "20260818_八小時課程"
        )
        self.assertEqual(server.course_picker_options("source", "mp3_parts")["mode"], "folder")

    def test_multi_part_manifest_merges_into_one_course(self):
        manifest, path = server.create_course_manifest(
            "mp3_parts",
            self._mp3_folder(5),
            "八小時課程",
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["multiPart"])
        # 分段錄音本身就是 MP3，不需要轉檔
        self.assertEqual(manifest["stages"]["media_to_mp3"]["status"], "skipped")
        # 5 段錄音只產生一份 manifest，不是每段一堂課
        self.assertEqual(len(list(Path(self.output_root).glob("*/course-manifest.json"))), 1)
        self.assertEqual(path.name, "course-manifest.json")
        self.assertIn("合併成單一份完整逐字稿",
                      manifest["stages"]["transcription"]["completion_criteria"])
        self.assertIn("同一堂課的分段",
                      manifest["stages"]["acquisition"]["completion_criteria"])

    def test_m4a_multi_part_manifest_merges_into_one_course(self):
        """m4a 分段錄音可直接轉錄，兩段仍只建立一份 manifest。"""
        folder = Path(self.temp_dir.name) / "m4a分段錄音"
        folder.mkdir()
        for name in ("part1.m4a", "part2.m4a"):
            (folder / name).write_bytes(b"fake m4a")

        manifest, path = server.create_course_manifest(
            "mp3_parts",
            str(folder),
            "m4a分段課程",
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["multiPart"])
        self.assertEqual(len(list(Path(self.output_root).glob("*/course-manifest.json"))), 1)
        self.assertEqual(path.name, "course-manifest.json")

    def test_mp3_folder_stays_one_course_per_file(self):
        """資料夾批次不能被誤改成合併，兩種語意要分開。"""
        manifest, _ = server.create_course_manifest(
            "mp3_folder",
            self._mp3_folder(3),
            "獨立課程批次",
            output_root=self.output_root,
        )
        self.assertFalse(manifest["options"]["multiPart"])
        self.assertNotIn("合併", manifest["stages"]["transcription"]["completion_criteria"])

    def test_course_page_offers_multi_part_source(self):
        self.assertIn('value="mp3_parts"', server.PAGE)
        self.assertTrue(
            "同一堂課的多個檔案" in server.PAGE,
            "多段來源標籤仍未改成『同一堂課的多個檔案』",
        )
        self.assertIn("mp3_parts:", server.PAGE)

    def test_review_is_opt_in_and_needs_transcript(self):
        """校對預設不做；勾了就自動補上逐字稿前置。"""
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "重要課程要校對",
            options={"artifacts": {"summary": True, "review": True}},
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["artifacts"]["review"])
        self.assertTrue(manifest["options"]["artifacts"]["transcript"])
        self.assertEqual(manifest["stages"]["transcript_review"]["status"], "pending")
        criterion = manifest["stages"]["transcript_review"]["completion_criteria"]
        self.assertIn("校對版", criterion)
        self.assertIn("raw", criterion)

    def test_review_runs_before_downstream_stages(self):
        """校對必須排在摘要之前，下游才吃得到校對稿。"""
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "順序測試",
            options={"artifacts": {"summary": True, "review": True}},
            output_root=self.output_root,
        )
        names = list(manifest["stages"].keys())
        self.assertLess(names.index("transcription"), names.index("transcript_review"))
        self.assertLess(names.index("transcript_review"), names.index("summary"))

    def test_transcript_engine_defaults_to_auto(self):
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "引擎預設",
            output_root=self.output_root,
        )
        self.assertEqual(manifest["options"]["transcriptEngine"], "auto")

    def test_transcript_engine_is_recorded_and_validated(self):
        for engine in ("auto", "subtitle_only", "groq", "assemblyai", "local_whisper"):
            manifest, _ = server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                f"引擎{engine}",
                options={"transcriptEngine": engine},
                output_root=self.output_root,
            )
            with self.subTest(engine=engine):
                self.assertEqual(manifest["options"]["transcriptEngine"], engine)

        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                "非法引擎",
                options={"transcriptEngine": "openai_paid"},
                output_root=self.output_root,
            )

    def test_assemblyai_engine_warns_no_srt_and_costs(self):
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "說話者辨識",
            options={"transcriptEngine": "assemblyai"},
            output_root=self.output_root,
        )
        criterion = manifest["stages"]["transcription"]["completion_criteria"]
        self.assertIn("說話者", criterion)
        self.assertIn("SRT", criterion)

    def test_subtitle_only_engine_requires_youtube(self):
        """只用現成字幕，本機來源根本沒有字幕可用。"""
        mp3 = Path(self.temp_dir.name) / "本機.mp3"
        mp3.write_bytes(b"fake")
        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "local_mp3", str(mp3), "本機不能只用字幕",
                options={"transcriptEngine": "subtitle_only"},
                output_root=self.output_root,
            )

    def test_engine_priority_has_sane_default(self):
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "順序預設",
            output_root=self.output_root,
        )
        self.assertEqual(manifest["options"]["enginePriority"],
                         server.DEFAULT_ENGINE_PRIORITY)
        # 預設不含要花時間或品質差的引擎
        for engine in ("local_whisper", "web", "subtitle_auto"):
            self.assertNotIn(engine, manifest["options"]["enginePriority"])

    def test_engine_priority_is_user_orderable(self):
        wanted = ["groq", "subtitle_manual", "local_whisper"]
        manifest, _ = server.create_course_manifest(
            "youtube",
            "https://www.youtube.com/watch?v=test",
            "自訂順序",
            options={"enginePriority": wanted},
            output_root=self.output_root,
        )
        self.assertEqual(manifest["options"]["enginePriority"], wanted)

    def test_paid_engine_cannot_enter_auto_fallback(self):
        """AssemblyAI 要錢，不能混進自動嘗試鏈，只能明確指定。"""
        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "youtube",
                "https://www.youtube.com/watch?v=test",
                "付費混進自動鏈",
                options={"enginePriority": ["groq", "assemblyai"]},
                output_root=self.output_root,
            )

    def test_engine_priority_rejects_unknown_and_empty(self):
        for bad in (["groq", "openai_paid"], []):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    server.create_course_manifest(
                        "youtube",
                        "https://www.youtube.com/watch?v=test",
                        f"非法順序{len(bad)}",
                        options={"enginePriority": bad},
                        output_root=self.output_root,
                    )

    def test_course_page_marks_assemblyai_as_paid_and_allows_reorder(self):
        self.assertIn("付費", server.PAGE)
        for token in ('id="coursePrioList"', 'id="coursePrioBox"',
                      "coursePrioMove(", "coursePrioToggle("):
            self.assertIn(token, server.PAGE)

    def _media_folder(self):
        """資料夾批次：每個檔各是一堂獨立的課，音訊影片混放。"""
        folder = Path(self.temp_dir.name) / "一批課程"
        folder.mkdir()
        for name in ("第一堂.mp3", "第二堂.mp4", "第三堂.mp3"):
            (folder / name).write_bytes(b"fake media")
        (folder / "說明.txt").write_text("不是媒體檔", encoding="utf-8")
        return str(folder)

    def test_course_folder_preview_lists_supported_top_level_media(self):
        """預覽只列第一層支援媒體，並穩定排序與分類。"""
        folder = Path(self.temp_dir.name) / "預覽混合媒體"
        folder.mkdir()
        for name in ("03-結尾.mp3", "01-開場.m4a", "02-畫面.mp4"):
            (folder / name).write_bytes(b"fake media")
        (folder / "課程說明.txt").write_text("不是媒體檔", encoding="utf-8")
        nested = folder / "子資料夾"
        nested.mkdir()
        (nested / "不應出現.mp3").write_bytes(b"nested media")

        preview = server.preview_course_folder(str(folder))

        self.assertEqual(preview["supportedCount"], 3)
        self.assertEqual(preview["ignoredCount"], 1)
        self.assertEqual(
            [item["name"] for item in preview["files"]],
            ["01-開場.m4a", "02-畫面.mp4", "03-結尾.mp3"],
        )
        self.assertEqual(
            [item["type"] for item in preview["files"]],
            ["audio", "video", "audio"],
        )
        for item in preview["files"]:
            self.assertIn("name", item)
            self.assertIn("type", item)

    def test_course_folder_preview_empty_folder_is_not_an_error(self):
        folder = Path(self.temp_dir.name) / "預覽空資料夾"
        folder.mkdir()

        preview = server.preview_course_folder(str(folder))

        self.assertEqual(preview["files"], [])
        self.assertEqual(preview["supportedCount"], 0)
        self.assertEqual(preview["ignoredCount"], 0)

    def test_batch_creates_one_manifest_per_media(self):
        """資料夾批次要每個媒體檔各建一份 manifest，不是只建一份。"""
        results = server.create_course_batch(
            "mp3_folder", self._media_folder(), output_root=self.output_root)
        self.assertEqual(len(results), 3)
        names = sorted(m["courseName"] for m, _ in results)
        self.assertEqual(names, ["第一堂", "第三堂", "第二堂"])
        # 非媒體檔不建
        self.assertEqual(len(list(Path(self.output_root).glob("*/course-manifest.json"))), 3)

    def test_mixed_media_batch_keeps_mp3_optional_per_source_file(self):
        """混合批次每個媒體各成課，只有原生 MP3 沒有轉檔產物可做。"""
        folder = Path(self.temp_dir.name) / "混合媒體課程"
        folder.mkdir()
        names = ("一.mp3", "二.m4a", "三.wav", "四.mp4", "五.mov")
        for name in names:
            (folder / name).write_bytes(b"fake media")

        results = server.create_course_batch(
            "mp3_folder",
            str(folder),
            options={"artifacts": {"mp3": True}},
            output_root=self.output_root,
        )
        self.assertEqual(len(results), len(names))
        mp3_flags = {
            Path(manifest["source"]["value"]).suffix.lower():
                manifest["options"]["artifacts"]["mp3"]
            for manifest, _ in results
        }
        self.assertFalse(mp3_flags[".mp3"])
        for suffix in (".m4a", ".wav", ".mp4", ".mov"):
            self.assertTrue(mp3_flags[suffix], suffix)

    def test_all_mp3_batch_disables_mp3_artifact(self):
        """整批來源全是 MP3 時，不能留下無事可做的轉檔 stage。"""
        results = server.create_course_batch(
            "mp3_folder",
            self._mp3_folder(2),
            options={"artifacts": {"mp3": True}},
            output_root=self.output_root,
        )
        self.assertEqual(len(results), 2)
        for manifest, _ in results:
            self.assertFalse(manifest["options"]["artifacts"]["mp3"])
            self.assertEqual(manifest["stages"]["media_to_mp3"]["status"], "skipped")

    def test_batch_shares_one_artifact_setting(self):
        """整批共用同一組產物勾選與引擎設定。"""
        results = server.create_course_batch(
            "mp3_folder", self._media_folder(), output_root=self.output_root,
            options={"artifacts": {"transcript": True, "summary": True,
                                   "report": False, "mindmap": False},
                     "transcriptEngine": "groq"})
        for manifest, _ in results:
            self.assertEqual(manifest["options"]["transcriptEngine"], "groq")
            self.assertEqual(manifest["stages"]["training_report"]["status"], "skipped")
            self.assertEqual(manifest["stages"]["summary"]["status"], "pending")

    def test_batch_rejects_empty_folder(self):
        empty = Path(self.temp_dir.name) / "空的"
        empty.mkdir()
        with self.assertRaises(ValueError):
            server.create_course_batch("mp3_folder", str(empty), output_root=self.output_root)

    def test_multi_part_is_not_batched(self):
        """分段錄音是一堂課，不可以被批次拆成多份。"""
        folder = Path(self.temp_dir.name) / "20260818_八小時"
        folder.mkdir()
        for i in range(1, 4):
            (folder / f"part{i}.mp3").write_bytes(b"fake")
        results = server.create_course_batch(
            "mp3_parts", str(folder), output_root=self.output_root)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][0]["options"]["multiPart"])

    def test_scan_course_progress_reads_manifests(self):
        """進度看板的資料來源是 manifest，不需要執行任何工作。"""
        server.create_course_batch(
            "mp3_folder", self._media_folder(), output_root=self.output_root)
        rows = server.scan_course_progress(self.output_root)
        self.assertEqual(len(rows), 3)
        row = rows[0]
        for key in ("courseName", "manifestPath", "totalStages", "doneStages",
                    "percent", "currentStage", "status"):
            self.assertIn(key, row)
        # 剛建立：全部 pending，完成 0%
        self.assertEqual(row["doneStages"], 0)
        self.assertEqual(row["percent"], 0)
        self.assertEqual(row["status"], "pending")

    def test_progress_counts_only_non_skipped_stages(self):
        """skipped 的階段不列入分母，否則永遠跑不到 100%。"""
        results = server.create_course_batch(
            "mp3_folder", self._media_folder(), output_root=self.output_root,
            options={"artifacts": {"transcript": True, "summary": False,
                                   "report": False, "mindmap": False}})
        manifest, path = results[0]
        total = sum(1 for v in manifest["stages"].values() if v["status"] != "skipped")

        # 模擬 AI 回寫：只動原本就要做的階段，skipped 的不碰
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["stages"]["media_to_mp3"]["status"], "skipped")
        data["stages"]["acquisition"]["status"] = "completed"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        row = next(r for r in server.scan_course_progress(self.output_root)
                   if r["manifestPath"] == str(path))
        self.assertEqual(row["totalStages"], total)
        self.assertEqual(row["doneStages"], 1)
        self.assertEqual(row["percent"], round(1 / total * 100))
        self.assertEqual(row["currentStage"], "transcription")
        self.assertEqual(row["status"], "running")

    def test_progress_marks_blocked_and_finished(self):
        results = server.create_course_batch(
            "mp3_folder", self._media_folder(), output_root=self.output_root,
            options={"artifacts": {"transcript": True, "summary": False,
                                   "report": False, "mindmap": False}})
        _, path = results[0]
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stages"]["acquisition"]["status"] = "completed"
        data["stages"]["transcription"]["status"] = "blocked"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        row = next(r for r in server.scan_course_progress(self.output_root)
                   if r["manifestPath"] == str(path))
        self.assertEqual(row["status"], "blocked")

        for name, stage in data["stages"].items():
            if stage["status"] != "skipped":
                stage["status"] = "completed"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        row = next(r for r in server.scan_course_progress(self.output_root)
                   if r["manifestPath"] == str(path))
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["percent"], 100)

    def test_course_page_has_batch_and_progress_ui(self):
        for token in ('id="courseBatch"', 'id="courseProgressBox"',
                      "refreshCourseProgress()", "/api/course/progress",
                      "/api/course/batch"):
            self.assertIn(token, server.PAGE)

    def test_decode_console_handles_cp950(self):
        """yt-dlp 在 Windows 走 cp950 輸出，寫死 utf-8 會整串變 U+FFFD。"""
        text = "師父商學院 EP65"
        self.assertEqual(server.decode_console(text.encode("cp950")), text)
        self.assertEqual(server.decode_console(text.encode("utf-8")), text)
        self.assertEqual(server.decode_console(b""), "")
        self.assertNotIn("\ufffd", server.decode_console(text.encode("cp950")))

    def test_video_artifact_and_quality(self):
        manifest, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "要影片",
            options={"artifacts": {"video": True, "transcript": True},
                     "videoQuality": "1080p"},
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["artifacts"]["video"])
        self.assertEqual(manifest["options"]["videoQuality"], "1080p")
        self.assertIn("mp4", manifest["options"]["youtubeArtifacts"])
        self.assertIn("1080p", manifest["stages"]["acquisition"]["completion_criteria"])

    def test_video_off_removes_mp4_from_artifacts(self):
        manifest, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "不要影片",
            options={"artifacts": {"video": False, "transcript": True}},
            output_root=self.output_root,
        )
        self.assertNotIn("mp4", manifest["options"]["youtubeArtifacts"])

    def test_video_unavailable_for_local_sources(self):
        mp3 = Path(self.temp_dir.name) / "本機音檔.mp3"
        mp3.write_bytes(b"fake")
        manifest, _ = server.create_course_manifest(
            "local_mp3", str(mp3), "本機來源",
            options={"artifacts": {"video": True, "transcript": True}},
            output_root=self.output_root,
        )
        self.assertFalse(manifest["options"]["artifacts"]["video"])

    def test_invalid_video_quality_rejected(self):
        with self.assertRaises(ValueError):
            server.create_course_manifest(
                "youtube", "https://www.youtube.com/watch?v=test", "怪解析度",
                options={"artifacts": {"video": True}, "videoQuality": "4320p"},
                output_root=self.output_root,
            )

    def test_summary_style_defaults_standard(self):
        manifest, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "摘要風格",
            output_root=self.output_root,
        )
        self.assertEqual(manifest["options"]["summaryStyle"], "standard")

    def test_summary_dense_style_changes_criteria(self):
        manifest, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "高密度摘要",
            options={"artifacts": {"summary": True}, "summaryStyle": "dense"},
            output_root=self.output_root,
        )
        self.assertEqual(manifest["options"]["summaryStyle"], "dense")
        self.assertIn("高密度", manifest["stages"]["summary"]["completion_criteria"])

    def test_raw_segments_is_opt_in_and_needs_transcript(self):
        manifest, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "原字分段",
            options={"artifacts": {"rawSegments": True}},
            output_root=self.output_root,
        )
        self.assertTrue(manifest["options"]["artifacts"]["rawSegments"])
        self.assertTrue(manifest["options"]["artifacts"]["transcript"])
        self.assertEqual(manifest["stages"]["raw_segments"]["status"], "pending")

        default, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "預設不做原字分段",
            output_root=self.output_root,
        )
        self.assertFalse(default["options"]["artifacts"]["rawSegments"])
        self.assertEqual(default["stages"]["raw_segments"]["status"], "skipped")

    def test_default_engine_priority_only_first_two_on(self):
        """使用者裁定：預設只走作者字幕與 Groq，其餘要自己勾。"""
        self.assertEqual(server.DEFAULT_ENGINE_PRIORITY, ["subtitle_manual", "groq"])
        manifest, _ = server.create_course_manifest(
            "youtube", "https://www.youtube.com/watch?v=test", "預設順序",
            output_root=self.output_root,
        )
        self.assertEqual(manifest["options"]["enginePriority"], ["subtitle_manual", "groq"])

    def test_course_page_has_video_and_summary_options(self):
        for token in ('id="a-video"', 'id="courseQuality"', 'value="1080p"',
                      'id="s-dense"', 'id="a-rawSegments"', 'id="courseSummarySub"'):
            self.assertIn(token, server.PAGE)

    def test_page_keys_cover_all_artifacts(self):
        """UI 的 keys 漏一個產物，那個勾選就會在載入時被靜默取消（實際踩過）。"""
        import re as _re
        m = _re.search(r"const keys = \[(.*?)\];", server.PAGE, _re.S)
        self.assertIsNotNone(m, "找不到 UI 的 keys 宣告")
        keys = set(_re.findall(r"'([a-zA-Z0-9]+)'", m.group(1)))
        self.assertEqual(keys, set(server.ARTIFACT_KEYS))

    def test_course_page_media_folder_copy_is_not_mp3_only(self):
        """資料夾來源的選項與提示不能再誤導成只收 MP3。"""
        for token in (
            "媒體資料夾批次（每個檔各是一堂課）",
            "同一堂課的多個檔案（分段錄音）",
        ):
            self.assertTrue(token in server.PAGE, f"UI 缺少媒體來源文案：{token}")
        for stale in (
            "MP3 資料夾批次（每個檔各是一堂課）",
            "選擇含 MP3 的資料夾，每個檔各建一堂課",
            "選擇資料夾，裡面的 MP3 依檔名排序合成同一堂課",
        ):
            self.assertTrue(stale not in server.PAGE, f"UI 仍殘留 MP3-only 文案：{stale}")
        self.assertTrue(
            "選擇 MP3 資料夾" not in server.COURSE_PICK_DIALOG_CODE,
            "資料夾選擇器仍顯示 MP3-only 標題",
        )

    def test_course_page_has_independent_folder_preview_flow(self):
        """選資料夾或手填路徑都會預覽；舊結果區不能兼任預覽容器。"""
        import re as _re

        for token in (
            'id="coursePreview"',
            'id="coursePreviewList"',
            "/api/course/preview",
            "previewCourseSource()",
            "clearCoursePreview()",
        ):
            self.assertTrue(token in server.PAGE, f"頁面缺少預覽契約：{token}")

        source_tag = _re.search(r'<input[^>]+id="courseSource"[^>]*>', server.PAGE)
        self.assertIsNotNone(source_tag, "找不到來源路徑 input")
        self.assertRegex(source_tag.group(0), r'onblur="[^"]*previewCourseSource\(\)')
        self.assertRegex(source_tag.group(0), r'oninput="[^"]*clearCoursePreview\(\)')

        picker = _re.search(
            r"async function pickCourseSource\(\) \{(.*?)"
            r"\nasync function pickCourseOutput\(\)",
            server.PAGE,
            _re.S,
        )
        self.assertIsNotNone(picker, "找不到 pickCourseSource()")
        picked_assignment = picker.group(1).find("$('courseSource').value = data.picked;")
        preview_call = picker.group(1).find("previewCourseSource()", picked_assignment)
        self.assertGreaterEqual(picked_assignment, 0, "picker 成功後沒有寫入來源路徑")
        self.assertGreater(preview_call, picked_assignment, "picker 成功後沒有觸發預覽")

        preview_fn = _re.search(
            r"(?:async )?function previewCourseSource\(\) \{(.*?)"
            r"\n(?:async )?function ",
            server.PAGE,
            _re.S,
        )
        self.assertIsNotNone(preview_fn, "找不到 previewCourseSource()")
        self.assertIn("$('coursePreview')", preview_fn.group(1))
        self.assertIn("$('coursePreviewList')", preview_fn.group(1))
        self.assertNotRegex(
            preview_fn.group(1),
            r"\$\('courseResult'\)\.(?:innerHTML|textContent)\s*=",
            "courseResult 不得當作資料夾檔案預覽容器",
        )

    def test_frontend_and_backend_do_not_require_mp3_for_transcript(self):
        """靜態釘住前後端相依，避免 UI 與 manifest 規則再次漂移。"""
        import re as _re
        self.assertNotEqual(server.ARTIFACT_REQUIRES.get("transcript"), "mp3")
        block = _re.search(
            r"function courseArtifactState\(\) \{(.*?)\n\}", server.PAGE, _re.S
        )
        self.assertIsNotNone(block, "找不到 courseArtifactState()")
        mp3_expr = _re.search(r"const mp3\s*=\s*(.*?);", block.group(1))
        self.assertIsNotNone(mp3_expr, "找不到 UI 的 mp3 產物運算式")
        self.assertNotIn("transcript", mp3_expr.group(1))
        self.assertNotIn("needTranscript", mp3_expr.group(1))

    def test_course_hash_autoroute_runs_after_required_state_initialization(self):
        """#course 自動點擊不能早於同步產物呼叫鏈所需的頂層狀態。"""
        hash_route = server.PAGE.find("if (location.hash === '#course')")
        self.assertNotEqual(hash_route, -1, "找不到 #course hash 自動路由")
        required_declarations = {
            "DEFAULTS": "const DEFAULTS =",
            "DOM helper $": "const $ =",
            "ENGINE_LABELS": "const ENGINE_LABELS =",
            "coursePrio": "let coursePrio =",
        }
        missing = [
            name for name, token in required_declarations.items()
            if server.PAGE.find(token) == -1
        ]
        self.assertEqual(missing, [], f"缺少必要的頂層初始化：{missing}")
        initialized_too_late = [
            name for name, token in required_declarations.items()
            if server.PAGE.find(token) > hash_route
        ]
        self.assertEqual(
            initialized_too_late,
            [],
            "#course 自動點擊發生在必要狀態初始化之前："
            + "、".join(initialized_too_late),
        )

    def test_course_page_has_engine_selector(self):
        for token in ('id="courseEngine"', 'value="groq"', 'value="assemblyai"',
                      'value="subtitle_only"', 'value="local_whisper"', 'id="courseEngineRow"'):
            self.assertIn(token, server.PAGE)

    def test_course_page_has_review_checkbox(self):
        self.assertIn('id="a-review"', server.PAGE)
        self.assertIn("校對", server.PAGE)

    def test_course_page_has_artifact_checkboxes(self):
        for token in (
            'id="a-mp3"',
            'id="a-transcript"',
            'id="a-summary"',
            'id="a-report"',
            'id="a-mindmap"',
            'id="a-skillTree"',
            'id="s-teach"',
            'id="s-minimum"',
            "syncCourseArtifacts()",
        ):
            self.assertIn(token, server.PAGE)
        # 舊的固定產物下拉選單已移除
        self.assertNotIn('id="courseSkillMode"', server.PAGE)

    def test_course_prompt_lists_only_selected_artifacts(self):
        self.assertIn("buildCoursePrompt", server.PAGE)
        self.assertIn("只做以下項目", server.PAGE)
        self.assertNotIn(
            "完成下載／轉檔、逐字稿、摘要、培訓報告、心智圖，以及我選擇的技能樹模式",
            server.PAGE,
        )

    def test_course_picker_options_use_file_or_folder_by_source(self):
        self.assertEqual(server.course_picker_options("source", "local_video")["mode"], "file")
        self.assertEqual(server.course_picker_options("source", "local_mp3")["mode"], "file")
        self.assertEqual(server.course_picker_options("source", "mp3_folder")["mode"], "folder")
        self.assertEqual(server.course_picker_options("output", "youtube")["mode"], "folder")
        with self.assertRaises(ValueError):
            server.course_picker_options("source", "youtube")


class TestCourseAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def test_api_health(self):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/api/health"
        req = urllib.request.urlopen(url)
        self.assertEqual(req.status, 200)
        data = json.loads(req.read().decode("utf-8"))
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "video-to-mp3")
        self.assertIn("courseRoot", data)

    def test_api_course_create_success(self):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/api/course/create"
        payload = json.dumps({
            "sourceType": "youtube",
            "source": "https://www.youtube.com/watch?v=test",
            "courseName": "API測試課程",
            "outputRoot": self.temp_dir.name
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            res = urllib.request.urlopen(req)
            self.assertEqual(res.status, 201)
            data = json.loads(res.read().decode("utf-8"))
            self.assertIn("manifestPath", data)
            self.assertIn("courseDir", data)
        except urllib.error.HTTPError as e:
            self.fail(f"HTTPError: {e.code} {e.read().decode('utf-8')}")

    def test_api_course_create_invalid(self):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/api/course/create"
        payload = json.dumps({
            "sourceType": "local_video",
            "source": r"C:\non_existent_file.mp4",
            "courseName": "無效來源課程"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_api_course_create_rejects_non_object_json(self):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/api/course/create"
        req = urllib.request.Request(
            url,
            data=b"[]",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_api_course_name_from_local_file(self):
        import urllib.request
        payload = json.dumps({
            "sourceType": "local_video",
            "source": r"D:\課程\Python 入門.mp4",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/course/name",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            self.assertEqual(json.loads(res.read().decode("utf-8"))["courseName"], "Python 入門")

    def test_api_course_pick_returns_path_and_derived_name(self):
        import urllib.request
        with mock.patch.object(server, "pick_course_path", return_value=r"D:\課程\第一章.mp3"):
            payload = json.dumps({"kind": "source", "sourceType": "local_mp3"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/course/pick",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as res:
                data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(data["picked"], r"D:\課程\第一章.mp3")
        self.assertEqual(data["courseName"], "第一章")

    def test_api_course_preview_returns_supported_media_contract(self):
        import urllib.request

        folder = Path(self.temp_dir.name) / "API預覽"
        folder.mkdir()
        for name in ("b.mp4", "a.m4a", "c.mp3"):
            (folder / name).write_bytes(b"fake")
        (folder / "ignore.txt").write_text("ignore", encoding="utf-8")
        payload = json.dumps({"source": str(folder)}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/course/preview",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))

        self.assertEqual(res.status, 200)
        self.assertEqual(data["supportedCount"], 3)
        self.assertEqual(data["ignoredCount"], 1)
        self.assertEqual([item["name"] for item in data["files"]],
                         ["a.m4a", "b.mp4", "c.mp3"])

    def test_api_course_preview_rejects_missing_or_non_folder_path(self):
        import urllib.error
        import urllib.request

        regular_file = Path(self.temp_dir.name) / "不是資料夾.m4a"
        regular_file.write_bytes(b"fake")
        missing = Path(self.temp_dir.name) / "不存在的資料夾"

        for source in (missing, regular_file):
            with self.subTest(source=source):
                payload = json.dumps({"source": str(source)}).encode("utf-8")
                req = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/api/course/preview",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code, 400)
                error = json.loads(ctx.exception.read().decode("utf-8"))["error"]
                self.assertTrue(error.strip())


    def test_api_course_batch_and_progress(self):
        import urllib.request
        import urllib.parse

        folder = Path(self.temp_dir.name) / "批次來源"
        folder.mkdir(exist_ok=True)
        for name in ("a.mp3", "b.mp4"):
            (folder / name).write_bytes(b"fake")
        out = Path(self.temp_dir.name) / "批次輸出"

        payload = json.dumps({
            "sourceType": "mp3_folder", "source": str(folder),
            "outputRoot": str(out),
            "options": {"artifacts": {"transcript": True, "summary": False,
                                      "report": False, "mindmap": False}},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/course/batch", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        data = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["manifestPaths"]), 2)

        url = (f"http://127.0.0.1:{self.port}/api/course/progress?root="
               + urllib.parse.quote(str(out)))
        prog = json.loads(urllib.request.urlopen(url).read().decode("utf-8"))
        self.assertEqual(prog["count"], 2)
        self.assertTrue(all(r["percent"] == 0 for r in prog["rows"]))
        self.assertTrue(all(r["status"] == "pending" for r in prog["rows"]))


class TestAdminIntegration(unittest.TestCase):
    def test_admin_has_pipeline_card_and_content_version(self):
        admin = Path(__file__).parents[2] / "tools" / "admin" / "index.html"
        html = admin.read_text(encoding="utf-8")
        self.assertIn("const CARD_CONTENT_VERSION = 3;", html)
        self.assertEqual(html.count("id: 'course-content-pipeline'"), 1)
        self.assertIn("http://127.0.0.1:8767/#course", html)


if __name__ == "__main__":
    unittest.main()
