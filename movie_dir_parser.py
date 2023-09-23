from pathlib import Path
from rich.console import Console
from rich.table import Table
import os
import re
import shutil
import transmission_rpc

# Clear completed movies from Transmission
print("---> Removing completed movies from Transmission...")
print("Output log:")
trans_client = transmission_rpc.Client(username='username',
                                       password='password',
                                       host='x.x.x.x',
                                       port='xxxx',
                                       protocol='http[s]')
incomplete_torrents_list = []
completed_torrents_dict = {}
for torrent in trans_client.get_torrents():
    if torrent.is_finished or 'seed' in torrent.status:
        completed_torrents_dict.update({torrent.name: torrent.id})
    else:
        incomplete_torrents_list.append(torrent.name)

for file, file_id in completed_torrents_dict.items():
    print(f"Removing:  {file}")
    trans_client.remove_torrent(file_id)

# Directory paths (mount points on SeedBox)
directory = '/$path/$location'
movies1 = '/$path/$location'
movies2 = '/$path/$location'
movies1_nas = '/$path/$location'
mov1_rec = '/$path/$location'
mov2_rec = '/$path/$location'

# Build current movie lists
print('\n')
print("---> Building movie lists...")
mov1_list = []
mov2_list = []
mov1_col_list = []
for file_name in os.listdir(movies1):
    mov1_list.append(file_name)

for file_name in os.listdir(movies2):
    mov2_list.append(file_name)

for file_dir in os.listdir(movies1_nas):
    if os.path.isdir(os.path.join(movies1_nas, file_dir)):
        for file_name in os.listdir(os.path.join(movies1_nas, file_dir)):
            mov1_col_list.append(file_name)

# Create a list of movies that have finished downloading
# and remove junk files
print("---> Deleting junk files...")
print("Output log:")
completed_list = []
skipped_list = []
for root, dirs, files in os.walk(directory):
    for file in files:
        if not file.endswith('.part') \
                and 'S0' not in file \
                and 'S1' not in file \
                and 'S2' not in file \
                and 'S3' not in file \
                and 'S4' not in file \
                and '.mp4' in file:
            directory_name = os.path.basename(root)
            if directory_name not in ['@eaDir', 'movies1_new', 'Subs', 'subs']:
                for file_name in os.listdir(os.path.join(directory, directory_name)):
                    current_dir = os.path.join(directory, directory_name)
                    if os.path.isfile(os.path.join(current_dir, file_name)):
                        if file_name in [f"{file_name}.nfo", 'RARBG_DO_NOT_MIRROR.exe', 'RARBG.txt',
                                         'RARBG_DO_NOT_MIRROR.exe@SynoResource',
                                         'RARBG.txt@SynoResource']:
                            removed_files = os.path.join(root, file_name)
                            print(f"Deleting:  {removed_files}")
                            os.remove(removed_files)
                # Add completed movies to list
                if '(' not in directory_name or ')' not in directory_name:
                    completed_list.append(directory_name)
                # Add to the skipped list on multiple passes
                if '(' in directory_name or ')' in directory_name:
                    if len(os.listdir(os.path.join(directory, directory_name))) > 1:
                        skipped_list.append(directory_name)
                    if len(os.listdir(os.path.join(directory, directory_name))) == 0:
                        try:
                            shutil.rmtree(os.path.join(directory, directory_name))
                        except OSError:
                            pass

        if '.nfs' in file:
            directory_name = os.path.basename(root)
            if directory_name not in ['@eaDir', 'movies1_new', 'Subs', 'subs']:
                print(f"{directory_name} may need removed from Transmission before dir can be removed")
                try:
                    empty_dir = Path(os.path.join(directory, directory_name))
                    empty_dir.rmdir()
                except OSError:
                    pass

# Rename directory of completed movie
# and output table showing results
table = Table(title="Updating Movies...")
table.add_column("Original", justify="left", style="cyan", min_width=50)
table.add_column("Changed", justify="left", style="yellow", min_width=50)
search_list = []
for movie_name in completed_list:
    split_item = movie_name.split('.')[:-3]
    if re.search('\\d\\d\\d\\d', split_item[-1]):
        year = split_item[-1]
        del split_item[-1]
        changed_dir_name = f"{' '.join([str(elem) for elem in split_item])} ({year})"
        original_dir_path = os.path.join(directory, movie_name)
        changed_dir_path = os.path.join(directory, changed_dir_name)
        if os.path.isdir(original_dir_path):
            os.rename(original_dir_path, changed_dir_path)
            table.add_row(movie_name, changed_dir_name)
            search_list.append(changed_dir_name)
    else:
        del split_item[-1]
        if re.search('\\d\\d\\d\\d', split_item[-1]):
            year = split_item[-1]
            del split_item[-1]
            changed_dir_name = f"{' '.join([str(elem) for elem in split_item])} ({year})"
            original_dir_path = os.path.join(directory, movie_name)
            changed_dir_path = os.path.join(directory, changed_dir_name)
            if os.path.isdir(original_dir_path):
                os.rename(original_dir_path, changed_dir_path)
                table.add_row(movie_name, changed_dir_name)
                search_list.append(changed_dir_name)
        else:
            del split_item[-1]
            year = split_item[-1]
            del split_item[-1]
            changed_dir_name = f"{' '.join([str(elem) for elem in split_item])} ({year})"
            original_dir_path = os.path.join(directory, movie_name)
            changed_dir_path = os.path.join(directory, changed_dir_name)
            if os.path.isdir(original_dir_path):
                os.rename(original_dir_path, changed_dir_path)
                table.add_row(movie_name, changed_dir_name)
                search_list.append(changed_dir_name)

console = Console()
print('\n')
console.print(table)

# Remove duplicates and empty dirs from movies1_new
print("---> Deleting duplicates...")
print("Output log:")
remove_list = []
failed_del_list = []
for movie_dir in search_list:
    removed_files = os.path.join(directory, movie_dir)
    try:
        if movie_dir in mov1_list:
            if os.path.isdir(removed_files):
                print(f"Deleting: {removed_files}  || exists in Library1")
                remove_list.append(movie_dir)
                shutil.rmtree(removed_files)
        elif movie_dir in mov2_list:
            if os.path.isdir(removed_files):
                print(f"Deleting: {removed_files}  || exists in Library2")
                remove_list.append(movie_dir)
                shutil.rmtree(removed_files)
        elif movie_dir in mov1_col_list:
            if os.path.isdir(removed_files):
                print(f"Deleting: {removed_files}  || exists in Collections")
                remove_list.append(movie_dir)
                shutil.rmtree(removed_files)
    except OSError:
        print(f"Couldn't delete {removed_files} trying empty dir removal...")
        remove_list.remove(movie_dir)
        try:
            empty_dir = Path(removed_files)
            empty_dir.rmdir()
        except OSError:
            failed_del_list.append(movie_dir)
            print("Yeah... that didn't work either...")
            pass
        pass

new_list = len(search_list) - len(remove_list) - len(failed_del_list)
table = Table(title="Results")
table.add_column("Finished", justify="left", style="cyan")
table.add_column("Remaining", justify="left", style="yellow")
table.add_column("Skipped", justify="left", style="green")
table.add_column("Deleted", justify="left", style="red")
table.add_column("New", justify="left", style="magenta")
table.add_row(str(len(search_list)), str(len(incomplete_torrents_list)), str(len(skipped_list)), str(len(remove_list)),
              str(new_list))
console = Console()
print('\n')
console.print(table)
