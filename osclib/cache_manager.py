from __future__ import print_function
import os
from osclib.common import NAME
import shutil
import sys
from time import time
from xdg.BaseDirectory import save_cache_path

# Provide general cache management in the form of directory location and pruned
# contents. Of the variety of caches utilized there will be content that will
# cease to be useful, but never get cleaned up since it will never be accessed.
# This manager ensures that the entire cache is pruned periodically to remove
# files that have not been accessed recently and avoid endless growth.
class CacheManager(object):
    DIRECTORY = save_cache_path(NAME)
    PRUNE_FREQUENCY = 60 * 60 * 24 * 7
    PRUNE_TTL = 60 * 60 * 24 * 30

    pruned = False

    @staticmethod
    def directory(*args):
        CacheManager.prune_all()
        return os.path.join(CacheManager.DIRECTORY, *args)

    @staticmethod
    def directory_test():
        if not CacheManager.DIRECTORY.endswith('.test'):
            CacheManager.DIRECTORY = os.path.join(CacheManager.DIRECTORY, '.test')

    @staticmethod
    def prune_all():
        if CacheManager.pruned:
            return
        CacheManager.pruned = True

        prune_lock = os.path.join(CacheManager.DIRECTORY, '.prune')
        if not os.path.exists(prune_lock):
            CacheManager.migrate()
        elif time() - os.stat(prune_lock).st_mtime < CacheManager.PRUNE_FREQUENCY:
            return

        with open(prune_lock, 'a'):
            os.utime(prune_lock, None)

        print('> pruning cache', file=sys.stderr)

        accessed_prune = time() - CacheManager.PRUNE_TTL
        files_pruned = 0
        bytes_pruned = 0
        for directory, subdirectories, files in os.walk(CacheManager.DIRECTORY):
            files_pruned_directory = 0
            for filename in files:
                path = os.path.join(directory, filename)
                stat = os.stat(path)
                accessed = stat.st_atime
                if accessed < accessed_prune:
                    files_pruned_directory += 1
                    files_pruned += 1
                    bytes_pruned += stat.st_size
                    os.remove(path)

            if len(subdirectories) == 0 and len(files) - files_pruned_directory == 0:
                os.rmdir(directory)

        print('> pruned {:,} files comprised of {:,} bytes'.format(
            files_pruned, bytes_pruned), file=sys.stderr)

    # Migrate the variety of prior cache locations within a single parent.
    @staticmethod
    def migrate(first=True):
        # If a old path exists then perform migration.
        cache_root = save_cache_path('')
        for source, destination in CacheManager.migrate_paths():
            if not os.path.exists(source):
                continue

            if first:
                print('> migrating caches', file=sys.stderr)

                # Move existing dir out of the way in order to nest.
                cache_moved = CacheManager.DIRECTORY + '-main'
                if not os.path.exists(cache_moved):
                    os.rename(CacheManager.DIRECTORY, cache_moved)

                # Detected need to migrate, but may have already passed -main.
                CacheManager.migrate(False)
                return

            # If either incompatible format, explicit removal, or newer source
            # was already migrated remove the cache entirely.
            if destination and os.path.exists(destination):
                # Set to None to make clear in message.
                destination = None

            print(
                '> - {} -> {}'.format(
                    os.path.relpath(source, cache_root),
                    os.path.relpath(destination, cache_root) if destination else None),
                file=sys.stderr)

            if not destination:
                shutil.rmtree(source)
                continue

            # Ensure parent directory exists and then move within.
            destination_parent = os.path.dirname(destination)
            if not os.path.exists(destination_parent):
                os.makedirs(destination_parent)
            os.rename(source, destination)

    @staticmethod
    def migrate_paths():
        path_map = {
            '{}-access': 'metrics-access',
            '{}-clone': 'request/clone',
            '{}-metrics': 'request/metrics',
            '{}-main': 'request/main',
            '{}-test': None,
            'opensuse-packagelists': 'pkglistgen',
            'opensuse-repo-checker': 'repository-meta',
            'opensuse-repo-checker-http': None,
            'osc-plugin-factory': None,
        }
        bases = [NAME, 'osc-plugin-factory']
        cache_root = save_cache_path('')
        for base in bases:
            for source, destination in path_map.items():
                source = os.path.join(cache_root, source.format(base))
                if destination:
                    destination = os.path.join(CacheManager.DIRECTORY, destination)

                yield source, destination
