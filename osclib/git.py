import os
from os import path
import subprocess
from xdg.BaseDirectory import save_cache_path

CACHE_DIR = save_cache_path('osc-plugin-factory', 'git')

def clone(url, directory):
    return_code = subprocess.call(['git', 'clone', url, directory])
    if return_code != 0:
        raise Exception('Failed to clone {}'.format(url))

def sync(cache_dir, repo_url, message=None):
    cwd = os.getcwd()
    devnull = open(os.devnull, 'wb')

    # Ensure git-sync tool is available.
    git_sync_dir = path.join(cache_dir, 'git-sync')
    git_sync_exec = path.join(git_sync_dir, 'git-sync')
    if not path.exists(git_sync_dir):
        os.makedirs(git_sync_dir)
        clone('https://github.com/simonthum/git-sync.git', git_sync_dir)
    else:
        os.chdir(git_sync_dir)
        subprocess.call(['git', 'pull', 'origin', 'master'], stdout=devnull, stderr=devnull)

    repo_name = path.basename(path.normpath(repo_url))
    repo_dir = path.join(cache_dir, repo_name)
    if not path.exists(repo_dir):
        os.makedirs(repo_dir)
        clone(repo_url, repo_dir)

        os.chdir(repo_dir)
        subprocess.call(['git', 'config', '--bool', 'branch.master.sync', 'true'])
        subprocess.call(['git', 'config', '--bool', 'branch.master.syncNewFiles', 'true'])
        if message:
            subprocess.call(['git', 'config', 'branch.master.syncCommitMsg', message])

    os.chdir(repo_dir)
    return_code = subprocess.call([git_sync_exec])
    if return_code != 0:
        raise Exception('failed to sync {}'.format(repo_name))

    os.chdir(cwd)

    return repo_dir
