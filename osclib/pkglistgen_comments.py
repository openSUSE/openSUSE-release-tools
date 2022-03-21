import textwrap
import re
import tempfile
import logging
import sys
from osclib.comments import CommentAPI
from osc.core import checkout_package

MARKER = 'PackageListDiff'


class PkglistComments(object):
    """Handling staging comments of diffs"""

    def __init__(self, apiurl):
        self.apiurl = apiurl
        self.comment = CommentAPI(apiurl)

    def read_summary_file(self, file):
        ret = dict()
        with open(file, 'r') as f:
            for line in f:
                pkg, group = line.strip().split(':')
                ret.setdefault(pkg, [])
                ret[pkg].append(group)
        return ret

    def write_summary_file(self, file, content):
        output = []
        for pkg in sorted(content):
            for group in sorted(content[pkg]):
                output.append(f"{pkg}:{group}")

        with open(file, 'w') as f:
            for line in sorted(output):
                f.write(line + '\n')

    def calculcate_package_diff(self, old_file, new_file):
        old_file = self.read_summary_file(old_file)
        new_file = self.read_summary_file(new_file)

        # remove common part
        keys = list(old_file.keys())
        for key in keys:
            if new_file.get(key, []) == old_file[key]:
                del new_file[key]
                del old_file[key]

        if not old_file and not new_file:
            return None

        removed = dict()
        for pkg in old_file:
            old_groups = old_file[pkg]
            if new_file.get(pkg):
                continue
            removekey = ','.join(old_groups)
            removed.setdefault(removekey, [])
            removed[removekey].append(pkg)

        report = ''
        for rm in sorted(removed.keys()):
            report += f"**Remove from {rm}**\n\n```\n"
            paragraph = ', '.join(removed[rm])
            report += "\n".join(textwrap.wrap(paragraph, width=90, break_long_words=False, break_on_hyphens=False))
            report += "\n```\n\n"

        moved = dict()
        for pkg in old_file:
            old_groups = old_file[pkg]
            new_groups = new_file.get(pkg)
            if not new_groups:
                continue
            movekey = ','.join(old_groups) + ' to ' + ','.join(new_groups)
            moved.setdefault(movekey, [])
            moved[movekey].append(pkg)

        for move in sorted(moved.keys()):
            report += f"**Move from {move}**\n\n```\n"
            paragraph = ', '.join(moved[move])
            report += "\n".join(textwrap.wrap(paragraph, width=90, break_long_words=False, break_on_hyphens=False))
            report += "\n```\n\n"

        added = dict()
        for pkg in new_file:
            if pkg in old_file:
                continue
            addkey = ','.join(new_file[pkg])
            added.setdefault(addkey, [])
            added[addkey].append(pkg)

        for group in sorted(added):
            report += f"**Add to {group}**\n\n```\n"
            paragraph = ', '.join(added[group])
            report += "\n".join(textwrap.wrap(paragraph, width=90, break_long_words=False, break_on_hyphens=False))
            report += "\n```\n\n"

        return report.strip()

    def handle_package_diff(self, project, old_file, new_file):
        comments = self.comment.get_comments(project_name=project)
        comment, _ = self.comment.comment_find(comments, MARKER)

        report = self.calculcate_package_diff(old_file, new_file)
        if not report:
            if comment:
                self.comment.delete(comment['id'])
            return 0
        report = self.comment.add_marker(report, MARKER)

        if comment:
            write_comment = report != comment['comment']
        else:
            write_comment = True
        if write_comment:
            if comment:
                self.comment.delete(comment['id'])
            self.comment.add_comment(project_name=project, comment=report)
        else:
            for c in comments.values():
                if c['parent'] == comment['id']:
                    ct = c['comment']
                    if ct.startswith('ignore ') or ct == 'ignore':
                        print(c)
                        return 0

        return 1

    def is_approved(self, comment, comments):
        for c in comments.values():
            if c['parent'] == comment['id']:
                ct = c['comment']
                if ct.startswith('approve ') or ct == 'approve':
                    return True
        return False

    def parse_title(self, line):
        m = re.match(r'\*\*Add to (.*)\*\*', line)
        if m:
            return {'cmd': 'add', 'to': m.group(1).split(','), 'pkgs': []}
        m = re.match(r'\*\*Move from (.*) to (.*)\*\*', line)
        if m:
            return {'cmd': 'move', 'from': m.group(1).split(','), 'to': m.group(2).split(','), 'pkgs': []}
        m = re.match(r'\*\*Remove from (.*)\*\*', line)
        if m:
            return {'cmd': 'remove', 'from': m.group(1).split(','), 'pkgs': []}
        return None

    def parse_sections(self, comment):
        current_section = None
        sections = []
        in_quote = False
        for line in comment.split('\n'):
            if line.startswith('**'):
                if current_section:
                    sections.append(current_section)
                current_section = self.parse_title(line)
                continue
            if line.startswith("```"):
                in_quote = not in_quote
                continue
            if in_quote:
                for pkg in line.split(','):
                    pkg = pkg.strip()
                    if pkg:
                        current_section['pkgs'].append(pkg)
        if current_section:
            sections.append(current_section)
        return sections

    def apply_move(self, content, section):
        for pkg in section['pkgs']:
            pkg_content = content[pkg]
            for group in section['from']:
                try:
                    pkg_content.remove(group)
                except ValueError:
                    logging.error(f"Can't remove {pkg} from {group}, not there. Mismatch.")
                    sys.exit(1)
            for group in section['to']:
                pkg_content.append(group)
            content[pkg] = pkg_content

    def apply_add(self, content, section):
        for pkg in section['pkgs']:
            content.setdefault(pkg, [])
            content[pkg] += section['to']

    def apply_remove(self, content, section):
        for pkg in section['pkgs']:
            pkg_content = content[pkg]
            for group in section['from']:
                try:
                    pkg_content.remove(group)
                except ValueError:
                    logging.error(f"Can't remove {pkg} from {group}, not there. Mismatch.")
                    sys.exit(1)
            content[pkg] = pkg_content

    def apply_commands(self, filename, sections):
        content = self.read_summary_file(filename)
        for section in sections:
            if section['cmd'] == 'move':
                self.apply_move(content, section)
            elif section['cmd'] == 'add':
                self.apply_add(content, section)
            elif section['cmd'] == 'remove':
                self.apply_remove(content, section)
        self.write_summary_file(filename, content)

    def check_staging_accept(self, project, target):
        comments = self.comment.get_comments(project_name=project)
        comment, _ = self.comment.comment_find(comments, MARKER)
        if not comment or not self.is_approved(comment, comments):
            return
        sections = self.parse_sections(comment['comment'])
        with tempfile.TemporaryDirectory() as tmpdirname:
            checkout_package(self.apiurl, target, '000package-groups', expand_link=True, outdir=tmpdirname)
            self.apply_commands(tmpdirname + '/summary-staging.txt', sections)
            raise RuntimeError("Not implemented commit")
