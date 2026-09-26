# movie_dir_parser

`movie_dir_parser` is a small Python utility for cleaning up downloaded movie folders and keeping a movie library organized.

It is built around a specific personal workflow:

- Transmission is used as the torrent client
- new downloads land in staging folders
- existing movie libraries are scanned for duplicates
- junk release files are removed
- completed movie folders are renamed into a cleaner format

## What the script does

On each run, the script can:

- connect to Transmission over RPC
- collect unfinished torrents that are not seeding or queued to seed
- remove finished torrents, torrents with `seed` in their status, and torrents with error code `3` from Transmission
- scan configured movie library folders
- scan configured staging/download folders
- delete common junk files such as `.txt`, `.exe`, and some release images
- rename messy release folder names into `Movie Title (Year)`
- detect when a renamed or already normalized movie exists in the main libraries
- delete duplicate movie folders from the staging folders
- remove empty folders beneath staging roots after cleanup
- print summary tables with `rich`
- send webhook notifications for renamed and deleted movies

## Example rename behavior

Examples of folder names the script tries to normalize:

```text
The.Movie.2009.1080p.BluRay.x265-RARBG
-> The Movie 2009 (2009)

Movie Title (2012) [1080p] [BluRay] [5.1] [YTS.MX]
-> Movie Title (2012)
```

The rename logic is pattern-based. It works best on common torrent/release naming formats that include a year and a `1080p` marker.

Folders containing `.mp4` or `.mkv` files are collected once per folder, even when they contain multiple videos. Extensions are case-insensitive, so `.MP4`, `.MKV`, and mixed-case variants are recognized. A folder whose name already ends with a parenthesized year, such as `Movie Title (2012)`, is counted as skipped for renaming but is still checked for duplicates. Names with release tags after the year remain eligible for renaming.

Movie folders can be nested inside grouping folders. Renames preserve the parent path: `Collection/Movie Title (2012) [1080p]` becomes `Collection/Movie Title (2012)`. Once a folder containing a video is found, its descendants are kept with it as extras rather than processed as separate movies. Loose videos directly in the staging root are not renamed, and `@eaDir` metadata directories are excluded from movie discovery.

If the destination folder already exists, the rename is skipped and the source stays unchanged. The attempted rename is not counted as finished and does not send a completion notification. The existing normalized destination is independently eligible for duplicate deletion when a library copy exists. Names that do not match a supported rename pattern are left unchanged.

## Directory configuration

The script reads its staging and library directories from environment variables.

Staging directories are provided through `NEW_MOVIE_DIRECTORIES`.

Library directories are provided through `MOVIES_DIRECTORIES`.

Both variables use a comma-separated list of absolute paths.

Example:

```bash
export NEW_MOVIE_DIRECTORIES="/path/new_movies1,/path/new_movies2"
export MOVIES_DIRECTORIES="/path/movies1/Library1,/path/movies2/Library2,/path/movies3/Library3,/path/movies1/Library1/__Unique_Collection"
```

The current script expects:

- at least 1 staging directory in `NEW_MOVIE_DIRECTORIES`
- at least 1 library directory in `MOVIES_DIRECTORIES`

Every staging directory receives junk cleanup, movie discovery, renaming, duplicate processing, and empty-folder cleanup. Both successfully renamed folders and already normalized folders are checked against the libraries. Only the staging copy is deleted.

When four or more library directories are configured, the fourth retains its special treatment as a nested collection. It is scanned recursively for `.mp4` and `.mkv` files regardless of extension case, excluding `@eaDir` directories and symbolic links. Other libraries are scanned for immediate child movie folders. Each qualifying folder must directly contain a regular video file; empty folders, loose files, and symlinks do not count as library copies. All configured libraries are included in duplicate detection and library totals.

All configured paths must exist and be directories. Staging roots must not overlap each other or any library root: identical paths, nested paths, and aliases resolving to such paths are rejected. Library roots may overlap each other, as in the collection example above. The script validates these rules and scans the libraries before connecting to Transmission or changing staging files.

## Duplicate deletion and empty-folder cleanup

For example, if `NEW_MOVIE_DIRECTORIES` includes `/downloads` and `MOVIES_DIRECTORIES` includes `/library`, then `/downloads/Collection/Movie Title (2012)` can be deleted when `/library/Movie Title (2012)` contains a video. No rename is required first. The library copy is kept.

Matching uses the full movie folder name, case-insensitively. Before each deletion, the script rechecks that a matching library folder still contains a regular `.mp4` or `.mkv` file and is outside staging. Copies found only in staging do not establish a retained library copy. Deletion rejects the staging root itself, absolute candidate paths, parent traversal, and candidates reached through symlinks or mount points.

This is a name-based duplicate check, not a content comparison: it does not compare checksums, editions, video quality, or playback validity. Keep different editions under distinct folder names. The retained copies must be in the configured libraries; the script does not search the entire filesystem.

After junk-file and duplicate cleanup, empty folders beneath each staging root are removed from the bottom up. Cleanup uses `rmdir`, which refuses to remove a nonempty folder, including one that gains a file after scanning. Staging roots, symlinks, mount points and their descendants, and folders containing any remaining files (including hidden files) are preserved. Empty grouping folders and folders emptied by junk cleanup are eligible. Permission errors are logged and processing continues. Empty-folder removals appear in the output log and the `Empty Removed` count; they do not send movie-deletion webhooks.

## Environment variables

The script reads these environment variables:

- `TRANSMISSION_USERNAME`
- `TRANSMISSION_PASSWORD`
- `TRANSMISSION_HOST`
- `TRANSMISSION_PORT` (defaults to `9091`; must be an integer from `1` to `65535`)
- `TRANSMISSION_PROTOCOL` (for example, `http`)
- `WEBHOOK_URL` (optional)
- `NEW_MOVIE_DIRECTORIES`
- `MOVIES_DIRECTORIES`

Set `WEBHOOK_URL` to a notification endpoint to receive rename and deletion events. Leave it unset or blank to disable notifications. Requests use a 10-second timeout; connection failures and unsuccessful HTTP responses are logged without interrupting movie processing. Notifications are not retried.

## Python dependencies

Install the packages used by the script:

```bash
python -m pip install -r requirements.txt
```

## Running the script

After configuring Transmission and, optionally, the webhook endpoint, run from the project directory:

```bash
export NEW_MOVIE_DIRECTORIES="/path/movies1_new,/media/movies3_new"
export MOVIES_DIRECTORIES="/path/movies1/Library1,/path/movies2/Library2,/path/movies3/Library3,/path/movies1/Library1/__Unique_Collection"
python movie_dir_parser.py
```

The script prints rename tables and a results table with these columns:

| Column | Meaning |
| --- | --- |
| Finished | Successfully renamed folders from all staging directories |
| Remaining | Unfinished torrents without `seed` in their status, counted before torrent removal |
| Skipped | Video folders already ending with a parenthesized year, including any subsequently deleted as duplicates |
| Deleted | Duplicate staging movie folders deleted, including already normalized folders |
| New | Successfully renamed folders remaining after duplicate deletion; deletion of older normalized folders does not reduce this count |
| New Total | Sum of qualifying video folders collected from the libraries before staging processing |
| Empty Removed | Empty descendant directories removed after staging processing |

`New Total` is the existing library count; it does not include newly renamed staging folders. Rename collisions and unsupported names are not included in the Skipped count. Deleted folder paths are printed in the output log.

## Example workflow

A typical run looks like this:

1. Validate directory paths and build lists of movies already present in the libraries.
2. Connect to Transmission.
3. Collect incomplete torrents.
4. Remove completed torrents from Transmission.
5. Delete junk files from staging folders.
6. Collect folders containing video files from every staging directory, counting already normalized names as skipped.
7. Rename matching folders into a cleaner format.
8. Check renamed and already normalized movies for matching library copies.
9. Delete duplicates from staging.
10. Remove empty folders beneath staging roots.
11. Print a results summary.

## Running tests

After installing the dependencies, run from the project directory:

```bash
python -m unittest -v
```

The test suite uses Python's built-in `unittest` framework. It creates temporary movie folders and mocks Transmission and HTTP calls, so it does not modify your media library or contact external services. The tests use `TestCase.enterContext`, which requires Python 3.11 or later.

Coverage includes environment parsing, directory validation, rename patterns, junk cleanup, normalized folders, multiple videos per folder, rename collisions, torrent status filtering, and duplicate deletion. It also covers optional webhooks and notification failures, nested folders and extras, uppercase video extensions, single-directory configurations, and processing additional staging and library directories. Cleanup tests cover normalized duplicates, missing library videos, overlapping paths, symlinks, mount points, nested empty folders, hidden files, permission errors, and files created just before empty-folder removal.

## Important limitations

Some workflow assumptions remain:

- Library ordering still matters: the fourth library directory receives special collection handling; other libraries are scanned only for immediate child movie folders containing videos.
- There is no config file. Configuration uses environment variables; validation covers nonempty directory lists, existing directories, staging path overlaps, and the Transmission port.
- There is no dry-run mode.
- The script deletes files and folders.
- Rename handling is based on a narrow set of filename patterns.
- Only `.mp4` and `.mkv` videos are recognized. A video folder's descendants are treated as extras, so grouping folders should not contain videos directly if their children represent separate movies.
- Video-file presence determines which staging folders are processed; the script does not cross-check those folders against incomplete torrents.
- Duplicate matching relies on movie folder names and video-file presence, not file contents or quality. Library checks and deletion are not atomic; avoid concurrent changes to retained library copies during a run.

## Safety notes

Use caution before pointing this at a real library.

Recommended first test:

1. Copy a few sample movie folders into a temporary test location.
2. Point `NEW_MOVIE_DIRECTORIES` and `MOVIES_DIRECTORIES` at that test data.
3. Run the script manually.
4. Verify renames and deletions.
5. Only then use it on real media folders.

## Project layout

- [movie_dir_parser.py](movie_dir_parser.py): main script
- [test_movie_dir_parser.py](test_movie_dir_parser.py): automated tests
- [requirements.txt](requirements.txt): runtime dependencies
- [README.md](README.md): project documentation
