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
- collect incomplete torrents
- remove completed or errored torrents from Transmission
- scan configured movie library folders
- scan configured staging/download folders
- delete common junk files such as `.txt`, `.exe`, and some release images
- rename messy release folder names into `Movie Title (Year)`
- detect when a renamed movie already exists in the main libraries
- delete duplicate movie folders from the staging folders
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

- at least 2 staging directories in `NEW_MOVIE_DIRECTORIES`
- at least 4 library directories in `MOVIES_DIRECTORIES`

Those minimums exist because `main()` still indexes into those lists directly.

## Environment variables

The script reads these environment variables:

- `TRANSMISSION_USERNAME`
- `TRANSMISSION_PASSWORD`
- `TRANSMISSION_HOST`
- `TRANSMISSION_PORT` (defaults to `9091`)
- `TRANSMISSION_PROTOCOL`
- `WEBHOOK_URL`
- `NEW_MOVIE_DIRECTORIES`
- `MOVIES_DIRECTORIES`

`WEBHOOK_URL` is optional in intent, but the current code posts directly to it during rename and deletion events. If you leave it unset, requests may fail at runtime unless you adjust the script.

## Python dependencies

Install the packages used by the script:

```bash
pip install -r requirements.txt
```

## Running the script

Run:

```bash
export NEW_MOVIE_DIRECTORIES="/path/movies1_new,/media/movies3_new"
export MOVIES_DIRECTORIES="/path/movies1/Library1,/path/movies2/Library2,/path/movies3/Library3,/path/movies1/Library1/__Unique_Collection"
python movie_dir_parser.py
```

The script prints tables showing:

- renamed movie folders
- deleted duplicates
- incomplete torrents still in Transmission
- skipped folders
- totals

## Example workflow

A typical run looks like this:

1. Connect to Transmission.
2. Collect incomplete torrents.
3. Remove completed torrents from Transmission.
4. Build lists of movies already present in the main libraries.
5. Delete junk files from staging folders and some library folders.
6. Find completed movie downloads in staging.
7. Rename matching folders into a cleaner format.
8. Check whether those renamed movies already exist in the library.
9. Delete duplicates from staging.
10. Print a results summary.

## Important limitations

This script is tightly coupled to one local environment.

- Directory paths now come from environment variables, but the script still assumes a fixed number and ordering of entries.
- There is no config file or schema validation beyond basic list parsing.
- There is no dry-run mode.
- The script deletes files and folders.
- Webhook posting is not guarded when `WEBHOOK_URL` is missing.
- Rename handling is based on a narrow set of filename patterns.

## Safety notes

Use caution before pointing this at a real library.

Recommended first test:

1. Copy a few sample movie folders into a temporary test location.
2. Point `NEW_MOVIE_DIRECTORIES` and `MOVIES_DIRECTORIES` at that test data.
3. Run the script manually.
4. Verify renames and deletions.
5. Only then use it on real media folders.

## Project layout

- [movie_dir_parser.py](/Users/sean/PycharmProjects/movie_dir_parser/movie_dir_parser.py): main script
- [README.md](/Users/sean/PycharmProjects/movie_dir_parser/README.md): project documentation
