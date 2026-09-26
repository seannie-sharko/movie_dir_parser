"""Run with python -m unittest -v; all filesystem changes use temporary folders."""

import os
from io import StringIO
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

with patch.dict(os.environ, {"TRANSMISSION_PORT": "9091"}):
    import movie_dir_parser as parser


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.library_temp = TemporaryDirectory()
        self.addCleanup(self.library_temp.cleanup)
        self.library_root = Path(self.library_temp.name).resolve()
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        # Prevent any test from making real HTTP or Transmission calls.
        self.post = contexts.enter_context(patch.object(parser.requests, "post"))
        self.client = contexts.enter_context(patch.object(parser.transmission_rpc, "Client"))
        contexts.enter_context(patch.object(parser, "WEBHOOK_URL", "https://example.invalid/notify"))

    def movie(self, name, files=("movie.mp4",)):
        directory = self.root / name
        directory.mkdir(parents=True)
        for filename in files:
            (directory / filename).touch()
        return directory

    def library_movies(self, *names):
        movies = []
        for name in names:
            directory = self.library_root / name
            directory.mkdir(parents=True)
            (directory / "movie.mp4").touch()
            movies.append(directory)
        return [movies]

    def test_environment_list_strips_whitespace_and_empty_entries(self):
        with patch.dict(os.environ, {"TEST_PATHS": " /one, ,/two ,"}):
            self.assertEqual(parser.parse_env_list("TEST_PATHS"), ["/one", "/two"])

    def test_environment_list_rejects_empty_value(self):
        with patch.dict(os.environ, {"TEST_PATHS": " , "}):
            with self.assertRaises(ValueError):
                parser.parse_env_list("TEST_PATHS")

    def test_documented_rename_examples(self):
        for source, expected in [
            ("The.Movie.2009.1080p.BluRay.x265-RARBG", "The Movie 2009 (2009)"),
            ("Movie Title (2012) [1080p] [BluRay] [5.1] [YTS.MX]", "Movie Title (2012)"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(parser.build_renamed_movie_name(source), expected)

    def test_unrecognized_name_is_ignored(self):
        self.assertIsNone(parser.build_renamed_movie_name("Movie without a year"))

    def test_missing_or_blank_webhook_disables_both_notifications(self):
        for url in (None, "", "   "):
            with self.subTest(url=url), patch.object(parser, "WEBHOOK_URL", url):
                self.assertFalse(parser.send_webhook_notification("Movie", "Completed", "today"))
                self.assertFalse(parser.send_deletion_notification("Movie", "staging", "today"))
        self.post.assert_not_called()

    def test_webhook_uses_timeout_and_preserves_payload(self):
        self.assertTrue(parser.send_webhook_notification("Movie", "Completed", "today"))
        self.post.assert_called_once_with(
            "https://example.invalid/notify",
            json={"title": "Movie", "message": "Completed on today", "priority": 2},
            timeout=10,
        )
        self.post.return_value.raise_for_status.assert_called_once()

    def test_notification_timeout_does_not_interrupt_renames(self):
        self.movie("First (2012) [1080p]")
        self.movie("Second (2013) [1080p]")
        self.post.side_effect = parser.requests.Timeout()
        _, renamed = parser.process_movies(
            str(self.root), ["First (2012) [1080p]", "Second (2013) [1080p]"]
        )
        self.assertEqual(renamed, ["First (2012)", "Second (2013)"])
        self.assertEqual(self.post.call_count, 2)
        self.assertTrue((self.root / "Second (2013)" / "movie.mp4").exists())

    def test_http_failure_does_not_interrupt_deletions(self):
        first = self.movie("First (2012)")
        second = self.movie("Second (2013)")
        self.post.return_value.raise_for_status.side_effect = parser.requests.HTTPError()
        removed = parser.process_deleted_movies(
            [first.name, second.name], self.library_movies(first.name, second.name), [str(self.root)]
        )
        self.assertEqual(removed, [str(first), str(second)])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_cleanup_preserves_movies_and_partial_text(self):
        directory = self.movie("Release", ("movie.mp4", "notes.txt", "setup.exe", "notes.part.txt"))
        parser.delete_junk_files(str(self.root))
        self.assertEqual({p.name for p in directory.iterdir()}, {"movie.mp4", "notes.part.txt"})

    def test_collect_release(self):
        self.movie("Movie.2012.1080p")
        self.assertEqual(parser.collect_completed_movies(str(self.root)), (["Movie.2012.1080p"], []))

    def test_already_normalized_movie_is_skipped(self):
        self.movie("Movie Title (2012)")
        self.assertEqual(parser.collect_completed_movies(str(self.root)), ([], ["Movie Title (2012)"]))

    def test_multiple_videos_produce_one_movie_entry(self):
        self.movie("Movie.2012.1080p", ("movie.mp4", "extra.mkv"))
        self.assertEqual(parser.collect_completed_movies(str(self.root)), (["Movie.2012.1080p"], []))

    def test_tagged_release_with_multiple_videos_is_processed_once(self):
        self.movie("Movie (2012) [1080p] [YTS.MX]", ("movie.mp4", "extra.mkv"))
        completed, skipped = parser.collect_completed_movies(str(self.root))
        self.assertEqual(skipped, [])
        _, renamed = parser.process_movies(str(self.root), completed)
        self.assertEqual(renamed, ["Movie (2012)"])
        self.post.assert_called_once()
        self.assertEqual(
            parser.collect_completed_movies(str(self.root)), ([], ["Movie (2012)"])
        )

    def test_nested_movie_is_renamed_in_place_with_extras(self):
        source = self.movie("Collection/Movie (2012) [1080p]", ("movie.MKV",))
        self.movie("Collection/Movie (2012) [1080p]/Extras", ("feature.MP4",))
        completed, skipped = parser.collect_completed_movies(str(self.root))
        self.assertEqual(completed, ["Collection/Movie (2012) [1080p]"])
        self.assertEqual(skipped, [])
        _, renamed = parser.process_movies(str(self.root), completed)
        self.assertEqual(renamed, ["Collection/Movie (2012)"])
        self.assertFalse(source.exists())
        self.assertTrue((self.root / "Collection/Movie (2012)/Extras/feature.MP4").exists())
        self.assertEqual(parser.collect_completed_movies(str(self.root)),
                         ([], ["Collection/Movie (2012)"]))

    def test_loose_videos_and_metadata_do_not_become_movie_folders(self):
        (self.root / "loose.MP4").touch()
        self.movie("@eaDir/Metadata (2012) [1080p]")
        self.movie("Movie (2012) [1080p]", ("movie.Mp4",))
        self.assertEqual(parser.collect_completed_movies(str(self.root)),
                         (["Movie (2012) [1080p]"], []))

    def test_nested_movies_with_same_name_keep_separate_paths(self):
        self.movie("One/Movie (2012) [1080p]")
        self.movie("Two/Movie (2012) [1080p]")
        completed, _ = parser.collect_completed_movies(str(self.root))
        _, renamed = parser.process_movies(str(self.root), completed)
        self.assertCountEqual(renamed, ["One/Movie (2012)", "Two/Movie (2012)"])
        removed = parser.process_deleted_movies(renamed, self.library_movies("Movie (2012)"), [str(self.root)])
        self.assertEqual(len(removed), 2)
        self.assertTrue((self.root / "One").is_dir())
        self.assertTrue((self.root / "Two").is_dir())

    def test_rename_moves_directory(self):
        source = self.movie("Release")
        destination = self.root / "Movie (2012)"
        self.assertEqual(parser.rename_movie_directory(source, destination), str(destination))
        self.assertFalse(source.exists())
        self.assertTrue((destination / "movie.mp4").exists())

    def test_rename_collision_preserves_both_directories(self):
        source = self.movie("Release")
        destination = self.movie("Movie (2012)", ("existing.mkv",))
        self.assertEqual(parser.rename_movie_directory(source, destination), str(source))
        self.assertTrue((source / "movie.mp4").exists())
        self.assertTrue((destination / "existing.mkv").exists())

    def test_process_collision_does_not_report_success(self):
        self.movie("Movie (2012) [1080p]")
        self.movie("Movie (2012)")
        _, renamed = parser.process_movies(str(self.root), ["Movie (2012) [1080p]"])
        self.assertEqual(renamed, [])
        self.post.assert_not_called()

    def test_process_success_renames_and_notifies(self):
        self.movie("Movie (2012) [1080p]")
        _, renamed = parser.process_movies(str(self.root), ["Movie (2012) [1080p]"])
        self.assertEqual(renamed, ["Movie (2012)"])
        self.assertTrue((self.root / "Movie (2012)" / "movie.mp4").exists())
        self.post.assert_called_once()

    def test_duplicates_deleted_and_unique_movies_preserved(self):
        duplicate = self.movie("Movie (2012)")
        unique = self.movie("Unique (2020)")
        removed = parser.process_deleted_movies(
            [duplicate.name, unique.name], self.library_movies("MOVIE (2012)"), [str(self.root)]
        )
        self.assertEqual(removed, [str(duplicate)])
        self.assertFalse(duplicate.exists())
        self.assertTrue(unique.exists())
        self.post.assert_called_once()

    def test_seeding_torrent_is_not_incomplete(self):
        client = Mock()
        client.get_torrents.return_value = [
            SimpleNamespace(name="Movie", is_finished=False, status="seeding", error=0, id=1)
        ]
        self.assertEqual(parser.collect_incomplete_movies(client), [])

    def test_library_names_are_candidates_and_video_validation_is_deferred(self):
        valid = self.library_movies("Real (2012)")[0][0]
        (self.library_root / "Empty (2013)").mkdir()
        (self.library_root / "File (2014)").touch()
        (self.library_root / "Link (2015)").symlink_to(valid, target_is_directory=True)
        linked_video = self.library_root / "Linked Video (2016)"
        linked_video.mkdir()
        (linked_video / "movie.mp4").symlink_to(valid / "movie.mp4")
        candidates = parser.build_movie_lists([self.library_root])
        self.assertCountEqual(candidates[0], [valid, self.library_root / "Empty (2013)", linked_video])
        empty_match = self.movie("Empty (2013)")
        linked_match = self.movie("Linked Video (2016)")
        self.assertEqual(parser.process_deleted_movies(
            [empty_match.name, linked_match.name], candidates, [self.root]
        ), [])
        self.assertTrue((empty_match / "movie.mp4").exists())
        self.assertTrue((linked_match / "movie.mp4").exists())

    def test_flat_library_scan_does_not_open_any_movie_folder(self):
        movies = self.library_movies("One (2012)", "Two (2013)")[0]
        empty = self.library_root / "Empty"
        empty.mkdir()
        (movies[0] / "Extras").mkdir()
        (movies[0] / "Extras/extra.mp4").touch()
        with patch.object(parser.os, "scandir", wraps=os.scandir) as scan:
            result = parser.build_movie_lists([self.library_root])
        self.assertCountEqual(result[0], movies)
        scan.assert_called_once_with(self.library_root)

    def test_recursive_collection_scan_does_not_rescan_directories(self):
        libraries = [self.movie(f"library{i}", ()) for i in range(4)]
        movie = self.movie("library3/Collection/Movie (2012)")
        self.movie("library3/@eaDir/Metadata (2013)")
        (libraries[3] / "linked").symlink_to(movie.parent, target_is_directory=True)
        with patch.object(parser.os, "scandir", wraps=os.scandir) as scan:
            result = parser.build_movie_lists(libraries)
        self.assertEqual(result, [[], [], [], [movie]])
        self.assertCountEqual([Path(call.args[0]) for call in scan.call_args_list],
                              libraries + [movie.parent])

    def test_large_library_only_opens_root_and_case_insensitive_match(self):
        for number in range(250):
            (self.library_root / f"Unrelated {number} (2000)").mkdir()
        retained = self.library_movies("THE RISE AND FALL OF THE CLASH (2012)")[0][0]
        staged = self.movie("The Rise And Fall Of The Clash (2012)")
        unique = self.movie("Unique (2020)")
        original_scandir = os.scandir
        library_scans = []

        def scan_only_needed_paths(path):
            # rmtree also calls scandir, using a file descriptor on supported systems.
            if not isinstance(path, int):
                path = Path(path)
                self.assertIn(path, (self.library_root, retained, staged))
                if path != staged:
                    library_scans.append(path)
            return original_scandir(path)

        with patch.object(parser.os, "scandir", side_effect=scan_only_needed_paths):
            candidates = parser.build_movie_lists([self.library_root])
            index = parser.build_movie_index(candidates)
            self.assertEqual(len(index), 251)
            self.assertIn(staged.name.lower(), index)
            removed = parser.process_deleted_movies(
                [staged.name, unique.name], candidates, [self.root], index
            )
        self.assertEqual(removed, [str(staged)])
        self.assertEqual(library_scans, [self.library_root, retained])
        self.assertTrue((retained / "movie.mp4").exists())
        self.assertTrue((unique / "movie.mp4").exists())

    def test_matching_empty_library_folder_does_not_hide_another_valid_copy(self):
        empty = self.library_root / "One/Movie (2012)"
        empty.mkdir(parents=True)
        retained = self.library_movies("Two/MOVIE (2012)")[0][0]
        staged = self.movie("Movie (2012)")
        libraries = [[empty], [retained]]
        index = parser.build_movie_index(libraries)
        self.assertEqual(len(index["movie (2012)"]), 2)
        self.assertEqual(parser.process_deleted_movies([staged.name], libraries, [self.root], index),
                         [str(staged)])
        self.assertTrue((retained / "movie.mp4").exists())

    def test_scan_progress_is_flushed_before_directory_access(self):
        movie = self.library_movies("Movie (2012)")[0][0]
        original_scandir = os.scandir
        output = StringIO()
        with patch.object(parser, "console", parser.Console(file=output, width=200)), \
                patch.object(output, "flush", wraps=output.flush) as flush:
            def scan_after_progress(path):
                self.assertIn("SCAN", output.getvalue())
                self.assertIn(str(path), output.getvalue())
                flush.assert_called()
                return original_scandir(path)

            with patch.object(parser.os, "scandir", side_effect=scan_after_progress):
                self.assertEqual(parser.build_movie_lists([self.library_root]), [[movie]])

    def test_library_scan_error_stops_before_torrent_or_staging_changes(self):
        staging = self.movie("staging", ("notes.txt",))
        library = self.movie("library", ())
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": str(staging),
            "MOVIES_DIRECTORIES": str(library),
        }), patch.object(parser.os, "scandir", side_effect=PermissionError("unavailable")):
            with self.assertRaises(PermissionError):
                parser.main()
        self.assertTrue((staging / "notes.txt").exists())
        self.client.assert_not_called()

    def test_library_stat_failure_preserves_staging_copy(self):
        movie = self.movie("Movie (2012)")
        library = self.library_movies(movie.name)[0][0]
        original_stat = os.stat

        def unavailable_stat(path, *args, **kwargs):
            if Path(path) == library:
                raise PermissionError("unavailable")
            return original_stat(path, *args, **kwargs)

        with patch.object(parser.os, "stat", side_effect=unavailable_stat):
            self.assertEqual(parser.process_deleted_movies([movie.name], [[library]], [self.root]), [])
        self.assertTrue((movie / "movie.mp4").exists())
        self.post.assert_not_called()

    def test_missing_library_video_preserves_staging_copy(self):
        movie = self.movie("Movie (2012)")
        library = self.library_movies(movie.name)[0][0]
        index = parser.build_movie_lists([self.library_root])
        (library / "movie.mp4").unlink()
        self.assertEqual(parser.process_deleted_movies([movie.name], index, [self.root]), [])
        self.assertTrue((movie / "movie.mp4").exists())
        self.post.assert_not_called()

    def test_library_replaced_by_symlink_preserves_staging_copy(self):
        movie = self.movie("Movie (2012)")
        library = self.library_movies(movie.name)[0][0]
        index = parser.build_movie_lists([self.library_root])
        (library / "movie.mp4").unlink()
        library.rmdir()
        library.symlink_to(movie, target_is_directory=True)
        self.assertEqual(parser.process_deleted_movies([movie.name], index, [self.root]), [])
        self.assertTrue((movie / "movie.mp4").exists())

    def test_copies_only_in_staging_do_not_trigger_deletion(self):
        first = self.movie("stage1/Movie (2012)")
        second = self.movie("stage2/Movie (2012)")
        self.assertEqual(parser.process_deleted_movies(
            [first.name], [[second]], [first.parent, second.parent]
        ), [])
        self.assertTrue((first / "movie.mp4").exists())
        self.assertTrue((second / "movie.mp4").exists())

    def test_deletion_rejects_root_absolute_paths_traversal_and_symlinks(self):
        outside = self.library_movies("Movie (2012)")[0][0]
        (self.root / "link").symlink_to(self.library_root, target_is_directory=True)
        for name in (".", str(outside), "../" + self.library_root.name,
                     "link", "link/Movie (2012)"):
            with self.subTest(name=name):
                self.assertIsNone(parser.delete_movie_directory(name, self.root))
        self.assertTrue(self.root.is_dir())
        self.assertTrue((outside / "movie.mp4").exists())
        self.assertTrue((self.root / "link").is_symlink())

    def test_empty_cleanup_removes_nested_folders_and_preserves_root(self):
        nested = self.movie("Empty/Nested/Leaf", ())
        self.movie("Another", ())
        removed = parser.remove_empty_directories(self.root)
        self.assertCountEqual(removed, [str(nested), str(nested.parent),
                                       str(self.root / "Empty"), str(self.root / "Another")])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(parser.remove_empty_directories(self.root), [])
        self.assertTrue(self.root.is_dir())

    def test_empty_cleanup_preserves_all_files_including_hidden_files(self):
        hidden = self.movie("Hidden", (".DS_Store",))
        text = self.movie("Text", ("notes.txt",))
        partial = self.movie("Partial", ("movie.mp4.part",))
        movie = self.movie("Movie (2012)")
        removed = parser.remove_empty_directories(self.root)
        self.assertEqual(removed, [])
        for directory in (hidden, text, partial, movie):
            self.assertTrue(any(directory.iterdir()))

    def test_empty_cleanup_does_not_follow_or_remove_links(self):
        external = self.library_root / "Empty"
        external.mkdir()
        container = self.movie("Links", ())
        (container / "external").symlink_to(self.library_root, target_is_directory=True)
        (container / "broken").symlink_to(self.library_root / "missing", target_is_directory=True)
        self.assertEqual(parser.remove_empty_directories(self.root), [])
        self.assertTrue(external.is_dir())
        self.assertTrue((container / "external").is_symlink())
        self.assertTrue((container / "broken").is_symlink())

    def test_empty_cleanup_does_not_descend_into_mounts(self):
        nested = self.movie("Mounted/Empty", ())
        mount = nested.parent
        with patch.object(Path, "is_mount", autospec=True,
                          side_effect=lambda path: path == mount):
            self.assertEqual(parser.remove_empty_directories(self.root), [])
        self.assertTrue(nested.is_dir())

    def test_empty_cleanup_preserves_file_created_just_before_removal(self):
        directory = self.movie("WasEmpty", ())
        original_rmdir = Path.rmdir

        def rmdir_after_write(path):
            (path / "new-file.bin").write_bytes(b"keep")
            original_rmdir(path)

        with patch.object(Path, "rmdir", autospec=True, side_effect=rmdir_after_write):
            self.assertEqual(parser.remove_empty_directories(self.root), [])
        self.assertEqual((directory / "new-file.bin").read_bytes(), b"keep")

    def test_empty_cleanup_continues_after_permission_error(self):
        blocked = self.movie("Blocked", ())
        removable = self.movie("Removable", ())
        original_rmdir = Path.rmdir

        def guarded_rmdir(path):
            if path == blocked:
                raise PermissionError(13, "Permission denied")
            original_rmdir(path)

        with patch.object(Path, "rmdir", autospec=True, side_effect=guarded_rmdir):
            self.assertEqual(parser.remove_empty_directories(self.root), [str(removable)])
        self.assertTrue(blocked.is_dir())

    def test_overlapping_configuration_is_rejected_before_any_changes(self):
        stage = self.movie("stage", ("notes.txt",))
        child = self.movie("stage/child", ())
        library = self.movie("library", ())
        alias = self.root / "stage_alias"
        alias.symlink_to(stage, target_is_directory=True)
        configurations = [
            ([stage], [stage]), ([stage], [child]), ([child], [stage]),
            ([stage], [alias]), ([stage, child], [library]),
            ([stage, alias], [library]),
        ]
        for staging, libraries in configurations:
            with self.subTest(staging=staging, libraries=libraries), patch.dict(os.environ, {
                "NEW_MOVIE_DIRECTORIES": ",".join(map(str, staging)),
                "MOVIES_DIRECTORIES": ",".join(map(str, libraries)),
            }):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    parser.main()
        self.assertTrue((stage / "notes.txt").exists())
        self.client.assert_not_called()
        self.post.assert_not_called()

    def test_normalized_duplicates_removed_without_renames_and_empty_parents_cleaned(self):
        staging = self.movie("staging", ())
        library = self.movie("library", ())
        retained = self.movie("library/MOVIE (2012)")
        duplicate = self.movie("staging/Collection/Movie (2012)")
        unique = self.movie("staging/Unique (2020)")
        self.movie("staging/Empty/Nested", ())
        self.movie("staging/JunkOnly", ("notes.txt",))
        self.movie("staging/Hidden", (".DS_Store",))
        self.client.return_value.get_torrents.return_value = []
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": str(staging),
            "MOVIES_DIRECTORIES": str(library),
        }), patch.object(parser.Console, "print") as print_table:
            parser.main()
        self.assertFalse(duplicate.exists())
        self.assertFalse(duplicate.parent.exists())
        self.assertFalse((staging / "Empty").exists())
        self.assertFalse((staging / "JunkOnly").exists())
        self.assertTrue((retained / "movie.mp4").exists())
        self.assertTrue((unique / "movie.mp4").exists())
        self.assertTrue((staging / "Hidden/.DS_Store").exists())
        self.assertTrue(staging.is_dir())
        results = print_table.call_args.args[0]
        self.assertEqual([column._cells[0] for column in results.columns],
                         ["0", "0", "2", "1", "0", "1", "4"])
        self.post.assert_called_once()
        self.assertIn("Deleted from", self.post.call_args.kwargs["json"]["message"])

    def test_incomplete_list_keeps_only_unfinished_nonseeding_torrents(self):
        client = Mock()
        client.get_torrents.return_value = [
            SimpleNamespace(name="Downloading", is_finished=False, status="downloading"),
            SimpleNamespace(name="Paused", is_finished=False, status="stopped"),
            SimpleNamespace(name="Finished", is_finished=True, status="stopped"),
            SimpleNamespace(name="Seeding", is_finished=False, status="seeding"),
            SimpleNamespace(name="Seed queued", is_finished=False, status="seed pending"),
        ]
        self.assertEqual(parser.collect_incomplete_movies(client), ["Downloading", "Paused"])

    def test_remove_completed_leaves_downloading_torrents(self):
        client = Mock()
        client.get_torrents.return_value = [
            SimpleNamespace(name="Complete", is_finished=False, status="seeding", error=0, id=1),
            SimpleNamespace(name="Downloading", is_finished=False, status="downloading", error=0, id=2),
        ]
        parser.remove_completed_movies(client)
        client.remove_torrent.assert_called_once_with(1)

    def test_main_accepts_documented_minimum_directories(self):
        staging = [self.movie(f"stage{i}", ()) for i in range(2)]
        libraries = [self.movie(f"library{i}", ()) for i in range(4)]
        self.client.return_value.get_torrents.return_value = []
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": ",".join(map(str, staging)),
            "MOVIES_DIRECTORIES": ",".join(map(str, libraries)),
        }), patch.object(parser.Console, "print"):
            parser.main()
        self.post.assert_not_called()

    def test_main_accepts_one_staging_and_one_library_without_webhook(self):
        staging = self.movie("staging", ())
        library = self.movie("library", ())
        self.movie("staging/Movie (2012) [1080p]")
        self.client.return_value.get_torrents.return_value = []
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": str(staging),
            "MOVIES_DIRECTORIES": str(library),
        }), patch.object(parser, "WEBHOOK_URL", None), patch.object(parser.Console, "print"):
            parser.main()
        self.assertTrue((staging / "Movie (2012)/movie.mp4").exists())
        self.post.assert_not_called()

    def test_invalid_directory_is_rejected_before_external_or_file_changes(self):
        staging = self.movie("staging", ("notes.txt",))
        not_directory = self.root / "file.txt"
        not_directory.touch()
        for invalid in (self.root / "missing", not_directory):
            with self.subTest(invalid=invalid), patch.dict(os.environ, {
                "NEW_MOVIE_DIRECTORIES": str(staging),
                "MOVIES_DIRECTORIES": str(invalid),
            }):
                with self.assertRaisesRegex(ValueError, "not a directory"):
                    parser.main()
        self.assertTrue((staging / "notes.txt").exists())
        self.client.assert_not_called()
        self.post.assert_not_called()

    def test_all_staging_directories_processed_without_cross_directory_deletion(self):
        staging = [self.movie(f"stage{i}", ()) for i in range(3)]
        library = self.movie("library", ())
        self.movie("library/Movie (2012)")
        # The release that collides remains; the normalized copy is a library duplicate.
        self.movie("stage0/Movie (2012) [1080p]")
        collision_source = self.movie("stage1/Movie (2012) [1080p]")
        collision_destination = self.movie("stage1/Movie (2012)")
        self.movie("stage2/Collection/Movie (2012) [1080p]", ("movie.MKV",))
        self.movie("stage2/Collection/Unique (2020) [1080p]", ("movie.MP4", "notes.txt"))
        self.client.return_value.get_torrents.return_value = []
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": ",".join(map(str, staging)),
            "MOVIES_DIRECTORIES": str(library),
        }), patch.object(parser.Console, "print") as print_table:
            parser.main()
        self.assertFalse((staging[0] / "Movie (2012)").exists())
        self.assertFalse((staging[2] / "Collection/Movie (2012)").exists())
        self.assertTrue((staging[2] / "Collection/Unique (2020)/movie.MP4").exists())
        self.assertFalse((staging[2] / "Collection/Unique (2020)/notes.txt").exists())
        self.assertTrue((collision_source / "movie.mp4").exists())
        self.assertFalse(collision_destination.exists())
        results_table = print_table.call_args.args[0]
        self.assertEqual([column._cells[0] for column in results_table.columns],
                         ["3", "0", "1", "3", "1", "1", "0"])

    def test_fourth_library_recognizes_uppercase_videos_once_and_ignores_metadata(self):
        staging = self.movie("staging", ())
        libraries = [self.movie(f"library{i}", ()) for i in range(4)]
        self.movie("library3/Collection/Movie (2012)", ("movie.MKV", "extra.MP4"))
        self.movie("library3/@eaDir/Other (2013)")
        self.movie("staging/Movie (2012) [1080p]")
        self.movie("staging/Other (2013) [1080p]")
        self.client.return_value.get_torrents.return_value = []
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": str(staging),
            "MOVIES_DIRECTORIES": ",".join(map(str, libraries)),
        }), patch.object(parser.Console, "print") as print_table:
            parser.main()
        self.assertFalse((staging / "Movie (2012)").exists())
        self.assertTrue((staging / "Other (2013)/movie.mp4").exists())
        results_table = print_table.call_args.args[0]
        self.assertEqual([column._cells[0] for column in results_table.columns],
                         ["2", "0", "0", "1", "1", "1", "0"])

    def test_main_accepts_more_than_four_library_directories(self):
        staging = [self.movie(f"stage{i}", ()) for i in range(2)]
        libraries = [self.movie(f"library{i}", ()) for i in range(5)]
        self.movie("library4/Movie (2012)")
        self.movie("stage0/Movie (2012) [1080p]")
        self.movie("stage0/Unique (2020) [1080p]")
        self.client.return_value.get_torrents.return_value = []
        with patch.dict(os.environ, {
            "NEW_MOVIE_DIRECTORIES": ",".join(map(str, staging)),
            "MOVIES_DIRECTORIES": ",".join(map(str, libraries)),
        }), patch.object(parser.Console, "print") as print_table:
            parser.main()
        self.assertFalse((staging[0] / "Movie (2012)").exists())
        self.assertFalse((staging[0] / "Movie (2012) [1080p]").exists())
        self.assertTrue((staging[0] / "Unique (2020)" / "movie.mp4").exists())
        self.assertTrue((libraries[4] / "Movie (2012)" / "movie.mp4").exists())
        results_table = print_table.call_args.args[0]
        self.assertEqual([column._cells[0] for column in results_table.columns],
                         ["2", "0", "0", "1", "1", "1", "0"])


if __name__ == "__main__":
    unittest.main()
