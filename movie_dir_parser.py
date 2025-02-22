from datetime import datetime as dt
from rich.console import Console
from rich.table import Table
import os
import re
import requests
import shutil
import transmission_rpc

# Constants
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
USERNAME = os.getenv("TRANSMISSION_USERNAME")
PASSWORD = os.getenv("TRANSMISSION_PASSWORD")
TRANSMISSION_HOST = os.getenv("TRANSMISSION_HOST")
TRANSMISSION_PORT = int(os.getenv("TRANSMISSION_PORT"))
TRANSMISSION_PROTOCOL = os.getenv("TRANSMISSION_PROTOCOL")


def build_movie_lists(movies_directories):
    """Build movie lists for specified directories."""
    movie_lists = []
    for directory in movies_directories:
        movie_list = []
        for file_name in os.listdir(directory):
            movie_list.append(file_name.lower())
        movie_lists.append(movie_list)
    return movie_lists


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
        if not torrent.is_finished or 'seed' not in torrent.status:
            incomplete_torrents_list.append(torrent.name)
    return incomplete_torrents_list


def collect_completed_movies(directory):
    """
    Collect completed and skipped movies and return a list of their dir names.
    (['Movie.Title.2012.1080p.BluRay.x265-RARBG'], [])
    """
    completed_list = []
    skipped_list = []
    # directory: '/media/movies3_new'
    # directory_name: Movie.Title.2012.1080p.BluRay.x265-RARBG or Movie Title (2012)
    # root: /media/movies3_new/Movie.Title.2012.1080p.BluRay.x265-RARBG
    # file(s): Movie.Title.2012.1080p.BluRay.x265-RARBG.mp4
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.mp4') or file.endswith('.mkv'):
                directory_name = os.path.basename(root)
                if directory_name not in ['(', ')']:
                    # Movie.Title.2012.1080p.BluRay.x265-RARBG
                    completed_list.append(directory_name)
                if directory_name in ['(', ')']:
                    if '[1080p]' in directory_name or '[yts.mx]' in directory_name:
                        # movie title (2012) [1080p] [bluray] [5.1] [yts.mx]
                        completed_list.append(directory_name)
                    if len(os.listdir(os.path.join(directory, directory_name))) > 0 and directory_name.endswith(')'):
                        # Movie Title (2012)
                        skipped_list.append(directory_name)
                    if len(os.listdir(os.path.join(directory, directory_name))) == 0:
                        try:
                            shutil.rmtree(os.path.join(directory, directory_name))
                        except OSError:
                            pass
    return completed_list, skipped_list


def delete_junk_files(directory):
    """Delete junk files from specified directory."""
    print(f"---> Deleting junk files in {directory}...")
    print("Output log:")
    for root, dirs, files in os.walk(directory):
        for file in files:
            if '.part' not in file \
                    and file.endswith('txt') \
                        or file.endswith('exe') \
                        or file.endswith('www.YTS.MX.jpg') \
                        or file.endswith('@SynoResource'):
                    print(f"Deleting:  {os.path.join(root, file)}")
                    os.remove(os.path.join(root, file))


# Helper Functions
def send_webhook_notification(title, message, cur_date):
    payload = {
        "title": title,
        "message": f"{message} on {cur_date}",
        "priority": 2
    }
    requests.post(WEBHOOK_URL, json=payload)


def generate_changed_name(movie_name, directory):
    """Generate the changed directory name for a movie."""
    original_dir_path = os.path.join(directory, movie_name)
    # Movie Title (2012) [1080p] [BluRay] [5.1] [YTS.MX]
    if ')' in movie_name and '[1080p]' in movie_name:
        changed_dir_name = re.sub(r'\[.*?\][^)]*$', '', movie_name).strip()
        return original_dir_path, changed_dir_name
    # Movie.Title.2012.1080p.BluRay.x265-RARBG
    # Movie.Title.2012.SPANISH.1080p.WEBRip.1600MB.DD5.1.x264-GalaxyRG
    for item in movie_name.split('.'):
        if '1080' in item:
            year = extract_year_before_1080p(movie_name, item)
            if year:
                split_list = movie_name.split('.')[:movie_name.split('.').index(item)]
                return original_dir_path, f"{' '.join(split_list)} ({year})"
    return None, None


def extract_year_before_1080p(movie_name, item):
    """Extract the year before the 1080p label."""
    for split_item in range(0, len(movie_name.split('.'))):
        year_match = re.search(r'[12][0-9]{3}', movie_name.split('.')[movie_name.split('.').index(item) - split_item])
        if year_match and '1080p' != year_match.group(0):
            return year_match.group(0)
    return None


def update_movie_directory(original_dir_path, changed_dir_path, movie_name, cur_date):
    """Update the movie directory and send a notification."""
    if os.path.isdir(original_dir_path):
        os.rename(original_dir_path, changed_dir_path)
        return changed_dir_path
    return None


def process_movies(directory, completed_list):
    now = dt.now()
    cur_date = now.strftime("%A %m/%d/%-Y @ %H:%M:%S")
    table = Table(title="Updating Movies...")
    table.add_column("Original", justify="left", style="cyan", min_width=50)
    table.add_column("Changed", justify="left", style="yellow", min_width=50)
    finished_list = []
    for movie_name in completed_list:
        original_dir_path, changed_dir_name = generate_changed_name(movie_name, directory)
        if original_dir_path and changed_dir_name:
            changed_dir_path = os.path.join(directory, changed_dir_name)
            updated_path = update_movie_directory(original_dir_path, changed_dir_path, movie_name, cur_date)
            if updated_path:
                table.add_row(movie_name, changed_dir_name)
                finished_list.append(changed_dir_name)
                send_webhook_notification(changed_dir_name, "Completed", cur_date)
    return table, finished_list


def send_deletion_notification(movie_name, movie_dir_name, cur_date):
    """Send a webhook notification when a movie directory is deleted."""
    payload = {
        "title": f"{movie_name}",
        "message": f"Deleted from {movie_dir_name} on {cur_date}",
        "priority": 2
    }
    requests.post(WEBHOOK_URL, json=payload)

def delete_movie_directory(movie_name, movie_dir_name):
    """Delete a movie directory if it exists."""
    removed_dir_path = os.path.join(movie_dir_name, movie_name)
    if os.path.isdir(removed_dir_path):
        print(f"Deleting: {removed_dir_path}")
        shutil.rmtree(removed_dir_path)
        return removed_dir_path
    return None

def process_deleted_movies(finished_list, all_movies, new_movie_directories):
    """Process movie deletions from specified directories."""
    now = dt.now()
    cur_date = now.strftime("%A %m/%d/%-Y @ %H:%M:%S")
    remove_list = []
    # Create a lookup for movie names in all_movies (for faster access)
    all_movie_names = {file.lower(): file for movie in all_movies for file in movie}
    for movie_name in finished_list:
        movie_name_lower = movie_name.lower()
        # Check if movie_name exists in any of the all_movies lists
        if movie_name_lower in all_movie_names:
            # For each directory, check if the directory for this movie exists and delete it
            for movie_dir_name in new_movie_directories:
                removed_path = delete_movie_directory(movie_name, movie_dir_name)
                if removed_path:
                    remove_list.append(removed_path)
                    send_deletion_notification(movie_name, movie_dir_name, cur_date)
    return remove_list


def main():
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

    # Directory paths (mount points on SeedBox)
    # Staging Dirs
    new_movie_directories = [
        '/media/movies1_new',
        '/media/movies3_new'
    ]
    # Home Dirs
    movies_directories = [
        '/media/movies1/Library1',
        '/media/movies2/Library2',
        '/media/movies3/Library3',
        '/media/movies1/Library1/__NAS_Collection'
    ]

    # Build current movie lists
    mov1_list, mov2_list, mov3_list, mov1_col_list = build_movie_lists(movies_directories)

    # Adjust a unique collection dir
    mov1_col_list = []
    for root, dirs, files in os.walk(movies_directories[3]):
        for file in files:
            if '.mp4' in file:
                if '/@eaDir' not in root:
                    mov1_col_list.append(root.split('/')[-1].lower())

    # Combine all movies together
    all_movies = [mov1_list, mov2_list, mov3_list, mov1_col_list]

    # Delete junk files
    for item in new_movie_directories:
        delete_junk_files(item)
    delete_junk_files('/media/movies3/Library3')
    delete_junk_files('/media/movies2/Library2')

    # Collect completed movies
    completed_list, skipped_list = collect_completed_movies(new_movie_directories[0])
    completed_list_3, skipped_list_3 = collect_completed_movies(new_movie_directories[1])

    # Rename completed movies
    table, finished_list = process_movies(new_movie_directories[0], completed_list)
    table_3, finished_list_3 = process_movies(new_movie_directories[1], completed_list)

    # Remove duplicates and empty dirs
    print('\n---> Deleting duplicate movies...')
    print('Output log:')
    remove_list = process_deleted_movies(finished_list, all_movies, new_movie_directories)
    remove_list_3 = process_deleted_movies(finished_list, all_movies, new_movie_directories)

    # Calculate total new movies (downloaded - removed duplicates)
    new_list = len(finished_list) + len(finished_list_3) - len(remove_list) - len(remove_list_3)

    # Output results
    results_table = Table(title="Results")
    results_table.add_column("Finished", justify="center", style="cyan")
    results_table.add_column("Remaining", justify="center", style="yellow")
    results_table.add_column("Skipped", justify="center", style="green")
    results_table.add_column("Deleted", justify="center", style="red")
    results_table.add_column("New", justify="center", style="magenta")
    results_table.add_column("New Total", justify="center", style="blue")
    results_table.add_row(
        str(len(finished_list) + len(finished_list_3)),  # Finished
        str(len(remaining_torrents)),  # Remaining
        str(len(skipped_list) + len(skipped_list_3)),  # Skipped (already renamed)
        str(len(remove_list) + len(remove_list_3)),  # Deleted
        str(new_list),  # Calculation of new movies
        str(len(all_movies[0]) + len(all_movies[1]) + len(all_movies[2]) + len(all_movies[3]))  # New grand total
    )

    console = Console()
    console.print(table)
    console.print(table_3)
    console.print(results_table)


if __name__ == "__main__":
    main()
