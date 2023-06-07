from rich.console import Console
from rich.table import Table
import os
import re

# Directory path
directory = '/media/movies1_new'

# Create a list of movies that haven't finished downloading
incomplete_list = []
for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith('.part') and '.mp4' in file:
            directory_name = os.path.basename(root)
            incomplete_list.append(directory_name)
            
# Create a list of movies that have finished downloading
# and remove junk files
completed_list = []
skipped_list = []
for root, dirs, files in os.walk(directory):
    for file in files:
        if not file.endswith('.part') and '.mp4' in file:
            directory_name = os.path.basename(root)
            if '@eaDir' not in directory_name:
              for file_name in os.listdir(os.path.join(directory, directory_name)):
                current_dir = os.path.join(directory, directory_name)   
                if os.path.isfile(os.path.join(current_dir, file_name)):
                  if file_name in ['RARBG_DO_NOT_MIRROR.exe', 'RARBG.txt', 'RARBG_DO_NOT_MIRROR.exe@SynoResource', 'RARBG.txt@SynoResource']
                    removed_files = os.path.join(root, file_name)
                    print(f"Deleting:  {removed_files}")
                    os.remove(removed_files)
            if directory_name not in ['@eaDir', 'movies1_new', 'Subs', 'subs']:
                if '(' not in directory_name or ')' not in directory_name:
                    completed_list.append(directory_name)
                if '(' in directory_name or ')' in directory_name:
                    skipped_list.append(directory_name)

# Rename directory of completed movie
# and output table showing results
table = Table(title="Updating Movies...")
table.add_column("Original", justify="left", style="cyan", no_wrap=True)
table.add_column("Changed", justify="left", style="yellow", no_wrap=True)
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

console = Console()
console.print(table)

table = Table(title="Results")
table.add_column("Finished", justify="left", style="cyan")
table.add_column("Remaining", justify="left", style="yellow")
table.add_column("Skipped", justify="left", style="green")
table.add_row(str(len(completed_list)), str(len(incomplete_list)), str(len(skipped_list)))
console = Console()
console.print(table)
