from datetime import datetime as dt
from rich.console import Console
from rich.table import Table
from pathlib import Path
import errno
import shutil
import os
import re
import stat
import time
import requests
import transmission_rpc

# Constants
VIDEO_EXTENSIONS = {".mp4", ".mkv"}
JUNK_SUFFIXES = (".exe", "www.YTS.MX.jpg", "@SynoResource", "Official site.jpg", "www.YTS.LT.jpg")
YEAR_PATTERN = re.compile(r"[12][0-9]{3}")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
USERNAME = os.getenv("TRANSMISSION_USERNAME")
PASSWORD = os.getenv("TRANSMISSION_PASSWORD")
TRANSMISSION_HOST = os.getenv("TRANSMISSION_HOST")
NEW_MOVIE_DIRECTORIES_ENV = "NEW_MOVIE_DIRECTORIES"
MOVIES_DIRECTORIES_ENV = "MOVIES_DIRECTORIES"
port_str = os.getenv("TRANSMISSION_PORT", "9091")

try:
    TRANSMISSION_PORT = int(port_str)
except ValueError:
    raise ValueError("TRANSMISSION_PORT must be an integer")

if not (1 <= TRANSMISSION_PORT <= 65535):
    raise ValueError("TRANSMISSION_PORT must be between 1 and 65535")
TRANSMISSION_PROTOCOL = os.getenv("TRANSMISSION_PROTOCOL")


def parse_env_list(env_var_name):
    """Parse a comma-separated environment variable into a list of paths."""
    raw_value = os.getenv(env_var_name, "")
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not values:
        raise ValueError(
            f"{env_var_name} must be set to a comma-separated list of directories"
        )
    return values


def build_movie_lists(movies_directories):
    """Scan each folder once, reusing scandir metadata and reporting progress."""
    movie_lists = []
    for index, directory in enumerate(movies_directories):
        base = Path(directory)
        recursive = index == 3
        started = time.monotonic()
        print(f"---> Scanning library {index + 1}/{len(movies_directories)}: {base}", flush=True)
        movies = []
        pending = [base]
        scanned_count = 0
        while pending:
            current = pending.pop()
            include_current = recursive or current != base
            found_video = False
            print(f"Scanning library folder: {current}", flush=True)
            # DirEntry type checks normally reuse directory-listing metadata,
            # avoiding separate Path.is_symlink/is_dir/is_file stat requests.
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name == '@eaDir':
                        continue
                    if (include_current and not found_video
                            and os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS
                            and entry.is_file(follow_symlinks=False)):
                        found_video = True
                        if not recursive:
                            break
                    if ((recursive or current == base)
                            and entry.is_dir(follow_symlinks=False)):
                        pending.append(Path(entry.path))
            scanned_count += 1
            if found_video:
                movies.append(current)
        movie_lists.append(movies)
        print(f"Library scan complete: {len(movies)} movie folders, "
              f"{scanned_count} directories scanned in {time.monotonic() - started:.1f}s", flush=True)
    return movie_lists


def has_video(directory):
    """An empty folder or a symlink is not evidence of a retained movie."""
    print(f"Checking retained library copy: {directory}", flush=True)
    try:
        if not stat.S_ISDIR(os.stat(directory, follow_symlinks=False).st_mode):
            return False
        with os.scandir(directory) as entries:
            return any(os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS
                       and entry.is_file(follow_symlinks=False) for entry in entries)
    except OSError as exc:
        print(f"Cannot verify library copy {directory}: {exc}", flush=True)
        return False


def paths_overlap(first, second):
    return first == second or first in second.parents or second in first.parents


def validate_directories(staging_directories, library_directories):
    """Resolve aliases and reject configurations that could delete retained media."""
    staging = []
    libraries = []
    for paths, resolved in ((staging_directories, staging), (library_directories, libraries)):
        for path in paths:
            print(f"Validating directory: {path}", flush=True)
            directory = Path(path).resolve()
            if not directory.is_dir():
                raise ValueError(f"Configured directory does not exist or is not a directory: {directory}")
            resolved.append(directory)
    for index, directory in enumerate(staging):
        if any(paths_overlap(directory, other) for other in staging[index + 1:] + libraries):
            raise ValueError(f"Staging directories must not overlap each other or library directories: {directory}")
    return list(map(str, staging)), list(map(str, libraries))


def staging_child(directory, relative_path):
    """Return a strict staging descendant, rejecting traversal and symlink paths."""
    base = Path(directory).resolve()
    relative = Path(relative_path)
    if relative.is_absolute() or '..' in relative.parts or relative == Path('.'):
        return None
    target = base
    for part in relative.parts:
        target /= part
        if target.is_symlink() or target.is_mount():
            return None
    if base not in target.resolve().parents:
        return None
    return target


def remove_empty_directories(directory):
    """Remove empty descendants with rmdir; preserve the staging root and links."""
    base = Path(directory).resolve()
    candidates = []
    for root, dirs, _ in os.walk(base, followlinks=False):
        dirs[:] = [name for name in dirs
                   if staging_child(base, (Path(root) / name).relative_to(base)) is not None]
        if Path(root) != base:
            candidates.append(Path(root).relative_to(base))
    removed = []
    for relative in reversed(candidates):
        target = staging_child(base, relative)
        if target is None:
            continue
        try:
            target.rmdir()
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                print(f"Skipping empty-folder cleanup for {target}: {exc.strerror}")
        else:
            removed.append(str(target))
            print(f"Removed empty directory: {target}")
    return removed


def remove_completed_movies(trans_client):
    """Remove completed movies from Transmission."""
    completed_torrents_dict = {}
    for torrent in trans_client.get_torrents():
        if torrent.is_finished \
                or 'seed' in torrent.status \
                or torrent.error == 3:
            completed_torrents_dict.update({torrent.name: torrent.id})
    for file, file_id in completed_torrents_dict.items():
        print(f"Removing:  {file}")
        trans_client.remove_torrent(file_id)


def collect_incomplete_movies(trans_client):
    """Collect incomplete movies from Transmission."""
    incomplete_torrents_list = []
    for torrent in trans_client.get_torrents():
        if not torrent.is_finished and 'seed' not in torrent.status:
            incomplete_torrents_list.append(torrent.name)
    return incomplete_torrents_list


def collect_completed_movies(directory):
    """Return movie paths relative to staging, keeping each movie's extras together."""
    completed_list = []
    skipped_list = []
    base = Path(directory)
    for root, dirs, files in os.walk(directory):
        dirs[:] = [name for name in dirs if name != '@eaDir']
        movie_path = Path(root)
        if movie_path == base:
            # Loose videos do not make the staging directory itself a movie.
            continue
        if not any(Path(file).suffix.lower() in VIDEO_EXTENSIONS for file in files):
            continue
        # A video folder and its descendants are one unit, including extras.
        dirs[:] = []
        relative_path = str(movie_path.relative_to(base))
        if re.search(r'\([12][0-9]{3}\)$', movie_path.name):
            skipped_list.append(relative_path)
        else:
            completed_list.append(relative_path)
    return completed_list, skipped_list


def delete_junk_files(directory):
    """Delete junk files from specified directory."""
    print(f"---> Deleting junk files in {directory}...")
    print("Output log:")
    for root, dirs, files in os.walk(directory):
        for file in files:
            if is_junk_file(file):
                file_path = os.path.join(root, file)
                print(f"Deleting:  {file_path}")
                os.remove(file_path)


# Helper Functions
def send_webhook_notification(title, message, cur_date):
    """Send an optional notification without interrupting filesystem processing."""
    if not WEBHOOK_URL or not WEBHOOK_URL.strip():
        return False
    payload = {
        "title": title,
        "message": f"{message} on {cur_date}",
        "priority": 2
    }
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Notification failed for {title}: {type(exc).__name__}")
        return False
    return True


def is_junk_file(file_name):
    """Return True when the file matches one of the cleanup patterns."""
    return (
            (file_name.endswith('.txt') and '.part' not in file_name)
            or file_name.endswith(JUNK_SUFFIXES)
    )


def build_renamed_movie_name(movie_name):
    """Generate a normalized directory name for a movie."""
    # Movie Title (2012) [1080p] [BluRay] [5.1] [YTS.MX]
    if ')' in movie_name and '[1080p]' in movie_name:
        return re.sub(r'\[.*?\][^)]*$', '', movie_name).strip()
    # Movie.Title.2012.1080p.BluRay.x265-RARBG
    # Movie.Title.2012.SPANISH.1080p.WEBRip.1600MB.DD5.1.x264-GalaxyRG
    name_parts = movie_name.split('.')
    for index, item in enumerate(name_parts):
        if '1080' in item:
            year = extract_release_year(name_parts, index)
            if year:
                return f"{' '.join(name_parts[:index])} ({year})"
    return None


def extract_release_year(name_parts, quality_index):
    """Extract the year before the 1080p label."""
    for candidate in reversed(name_parts[:quality_index]):
        year_match = YEAR_PATTERN.search(candidate)
        if year_match:
            return year_match.group(0)
    return None


def rename_movie_directory(original_dir_path, changed_dir_path):
    """Rename a movie directory, returning its unchanged path when skipped."""
    original = Path(original_dir_path)
    changed = Path(changed_dir_path)

    if original == changed:
        return str(original)

    if changed.exists() or changed.is_symlink():
        print(f"Skipping rename: destination already exists: {changed}")
        return str(original)

    original.rename(changed)
    return str(changed)


def process_movies(directory, completed_movies):
    now = dt.now()
    cur_date = now.strftime("%A %m/%d/%-Y @ %H:%M:%S")

    table = Table(title="Updating Movies...")
    table.add_column("Original", justify="left", style="cyan", min_width=50)
    table.add_column("Changed", justify="left", style="yellow", min_width=50)
    renamed_movies = []
    for movie_name in completed_movies:
        movie_path = Path(movie_name)
        changed_dir_name = build_renamed_movie_name(movie_path.name)
        if not changed_dir_name:
            continue

        original_dir_path = os.path.join(directory, movie_name)
        changed_relative_path = str(movie_path.with_name(changed_dir_name))
        changed_dir_path = os.path.join(directory, changed_relative_path)
        updated_path = rename_movie_directory(original_dir_path, changed_dir_path)
        if not updated_path or Path(updated_path) == Path(original_dir_path):
            continue

        table.add_row(movie_name, changed_relative_path)
        renamed_movies.append(changed_relative_path)
        send_webhook_notification(changed_dir_name, "Completed", cur_date)
    return table, renamed_movies


def send_deletion_notification(movie_name, movie_dir_name, cur_date):
    """Send a webhook notification when a movie directory is deleted."""
    return send_webhook_notification(
        Path(movie_name).name, f"Deleted from {movie_dir_name}", cur_date
    )


def delete_movie_directory(movie_name, movie_dir_name):
    """Delete only a real directory strictly beneath the staging root."""
    removed_dir_path = staging_child(movie_dir_name, movie_name)
    if removed_dir_path is not None and removed_dir_path.is_dir():
        print(f"Deleting: {removed_dir_path}")
        shutil.rmtree(removed_dir_path)
        return str(removed_dir_path)
    return None


def process_deleted_movies(finished_list, all_movies, new_movie_directories):
    """Delete candidates only while a matching external library movie still exists."""
    now = dt.now()
    cur_date = now.strftime("%A %m/%d/%-Y @ %H:%M:%S")
    remove_list = []
    library_by_name = {}
    staging_roots = [Path(directory).resolve() for directory in new_movie_directories]
    for movies in all_movies:
        for movie in movies:
            library_path = Path(movie)
            library_by_name.setdefault(library_path.name.casefold(), []).append(library_path)
    for movie_name in finished_list:
        matches = library_by_name.get(Path(movie_name).name.casefold(), [])
        for movie_dir_name in new_movie_directories:
            # Recheck for each deletion; a library folder may have disappeared.
            retained = any(
                has_video(library)
                and not any(paths_overlap(library.resolve(), root) for root in staging_roots)
                and not any(parent.is_symlink() for parent in library.parents)
                for library in matches
            )
            if not retained:
                continue
            removed_path = delete_movie_directory(movie_name, movie_dir_name)
            if removed_path:
                remove_list.append(removed_path)
                send_deletion_notification(movie_name, movie_dir_name, cur_date)
    return remove_list


def main():
    new_movie_directories = parse_env_list(NEW_MOVIE_DIRECTORIES_ENV)
    movies_directories = parse_env_list(MOVIES_DIRECTORIES_ENV)
    new_movie_directories, movies_directories = validate_directories(
        new_movie_directories, movies_directories
    )

    # Scan libraries before making any changes to torrents or staging folders.
    all_movies = build_movie_lists(movies_directories)

    trans_client = transmission_rpc.Client(
        username=USERNAME,
        password=PASSWORD,
        host=TRANSMISSION_HOST,
        port=TRANSMISSION_PORT,
        protocol=TRANSMISSION_PROTOCOL)

    # Collect remaining movies from Transmission
    remaining_torrents = collect_incomplete_movies(trans_client)

    # Clear completed movies from Transmission
    remove_completed_movies(trans_client)

    tables = []
    finished_by_directory = []
    skipped_count = 0
    for directory in new_movie_directories:
        delete_junk_files(directory)
        completed, skipped = collect_completed_movies(directory)
        table, finished = process_movies(directory, completed)
        tables.append(table)
        finished_by_directory.append((directory, finished, skipped))
        skipped_count += len(skipped)

    # Check both newly renamed and already normalized movies in their own staging directory.
    print('\n---> Deleting duplicate movies...')
    print('Output log:')
    remove_list = []
    removed_new_count = 0
    for directory, finished, skipped in finished_by_directory:
        removed_new = process_deleted_movies(finished, all_movies, [directory])
        removed_new_count += len(removed_new)
        remove_list.extend(removed_new)
        remove_list.extend(process_deleted_movies(skipped, all_movies, [directory]))

    empty_removed = []
    for directory in new_movie_directories:
        empty_removed.extend(remove_empty_directories(directory))

    # Calculate total new movies (downloaded - removed duplicates)
    finished_count = sum(len(finished) for _, finished, _ in finished_by_directory)
    new_list = finished_count - removed_new_count

    # Output results
    results_table = Table(title="Results")
    results_table.add_column("Finished", justify="center", style="cyan")
    results_table.add_column("Remaining", justify="center", style="yellow")
    results_table.add_column("Skipped", justify="center", style="green")
    results_table.add_column("Deleted", justify="center", style="red")
    results_table.add_column("New", justify="center", style="magenta")
    results_table.add_column("New Total", justify="center", style="blue")
    results_table.add_column("Empty Removed", justify="center", style="green")
    results_table.add_row(
        str(finished_count),  # Finished
        str(len(remaining_torrents)),  # Remaining
        str(skipped_count),  # Skipped (already renamed)
        str(len(remove_list)),  # Deleted
        str(new_list),  # Calculation of new movies
        str(sum(len(movies) for movies in all_movies)),  # Existing library count
        str(len(empty_removed)),
    )

    console = Console()
    for table in tables:
        console.print(table)
    console.print(results_table)


if __name__ == "__main__":
    main()
