import datetime
import enum
import logging
import os
import re
import sys
import tempfile
import textwrap
from typing import Dict, List, Optional

from lxml import etree as ET
from osc.core import Package, checkout_package, http_GET, makeurl

from osclib.comments import CommentAPI
from osclib.core import devel_project_get

MARKER = 'PackageListDiff'


class PkglistSectionCommend(enum.Enum):
    ADD = "add"
    REMOVE = "remove"
    MOVE = "move"


class PkglistSection:
    def __init__(
        self,
        command: PkglistSectionCommend,
        pkgs: Optional[List[str]] = None,
        to_module: Optional[List[str]] = None,
        from_module: Optional[List[str]] = None
    ):
        self.command = command
        if pkgs is None:
            self.pkgs = []
        else:
            self.pkgs = pkgs
        if pkgs is None:
            self.to_module = []
        else:
            self.to_module = to_module
        if pkgs is None:
            self.from_module = []
        else:
            self.from_module = from_module


class PkglistComments:
    """Handling staging comments of diffs"""

    def __init__(self, apiurl: str):
        self.apiurl = apiurl
        self.comment = CommentAPI(apiurl)

    def read_summary_file(self, file: str) -> Dict[str, List[str]]:
        ret = dict()
        with open(file, 'r') as f:
            for line in f:
                pkg, group = line.strip().split(':')
                ret.setdefault(pkg, [])
                ret[pkg].append(group)
        return ret

    def write_summary_file(self, file: str, content: dict):
        output = []
        for pkg in sorted(content):
            for group in sorted(content[pkg]):
                output.append(f"{pkg}:{group}")

        with open(file, 'w') as f:
            for line in sorted(output):
                f.write(line + '\n')

    def calculcate_package_diff(self, old_file: str, new_file: str):
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

    def handle_package_diff(self, project: str, old_file: str, new_file: str):
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
                    if ct.startswith('approve ') or ct == 'approve':
                        print(c)
                        return 0

        return 1

    def is_approved(self, comment, comments: dict) -> str | None:
        if not comment:
            return None

        for c in comments.values():
            if c['parent'] == comment['id']:
                ct = c['comment']
                if ct.startswith('approve ') or ct == 'approve':
                    return c['who']
        return None

    def parse_title(self, line: str) -> Optional[PkglistSection]:
        m = re.match(r'\*\*Add to (.*)\*\*', line)
        if m:
            return PkglistSection(PkglistSectionCommend.ADD, pkgs=[], to_module=m.group(1).split(','))
        m = re.match(r'\*\*Move from (.*) to (.*)\*\*', line)
        if m:
            return PkglistSection(
                PkglistSectionCommend.MOVE,
                pkgs=[],
                from_module=m.group(1).split(','),
                to_module=m.group(2).split(','),
            )
        m = re.match(r'\*\*Remove from (.*)\*\*', line)
        if m:
            return PkglistSection(
                PkglistSectionCommend.REMOVE,
                pkgs=[],
                from_module=m.group(1).split(','),
            )
        return None

    def parse_sections(self, comment: str) -> List[PkglistSection]:
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
                        current_section.pkgs.append(pkg)
        if current_section:
            sections.append(current_section)
        return sections

    def apply_move(self, content: Dict[str, List[str]], section: PkglistSection):
        for pkg in section.pkgs:
            pkg_content = content[pkg]
            for group in section.from_module:
                try:
                    pkg_content.remove(group)
                except ValueError:
                    logging.error(f"Can't remove {pkg} from {group}, not there. Mismatch.")
                    sys.exit(1)
            for group in section.to_module:
                pkg_content.append(group)
            content[pkg] = pkg_content

    def apply_add(self, content: Dict[str, List[str]], section: PkglistSection):
        for pkg in section.pkgs:
            content.setdefault(pkg, [])
            content[pkg] += section.to_module

    def apply_remove(self, content: Dict[str, List[str]], section: PkglistSection):
        for pkg in section.pkgs:
            pkg_content = content[pkg]
            for group in section.from_module:
                try:
                    pkg_content.remove(group)
                except ValueError:
                    logging.error(f"Can't remove {pkg} from {group}, not there. Mismatch.")
                    sys.exit(1)
            content[pkg] = pkg_content

    def apply_commands(self, filename: str, sections: List[PkglistSection]):
        content = self.read_summary_file(filename)
        for section in sections:
            if section.command == PkglistSectionCommend.MOVE:
                self.apply_move(content, section)
            elif section.command == PkglistSectionCommend.ADD:
                self.apply_add(content, section)
            elif section.command == PkglistSectionCommend.REMOVE:
                self.apply_remove(content, section)
        self.write_summary_file(filename, content)

    def format_pkgs(self, pkgs: List[str]):
        text = ', '.join(pkgs)
        return "  " + "\n  ".join(textwrap.wrap(text, width=68, break_long_words=False, break_on_hyphens=False)) + "\n\n"

    def format_move(self, section: PkglistSection):
        gfrom = ','.join(section.from_module)
        gto = ','.join(section.to_module)
        text = f"  * Move from {gfrom} to {gto}:\n"
        return text + self.format_pkgs(section.pkgs)

    def format_add(self, section: PkglistSection):
        gto = ','.join(section.to_module)
        text = f"  * Add to {gto}:\n"
        return text + self.format_pkgs(section.pkgs)

    def format_remove(self, section: PkglistSection):
        gfrom = ','.join(section.from_module)
        text = f"  * Remove from {gfrom}:\n"
        return text + self.format_pkgs(section.pkgs)

    def apply_changes(self, filename: str, sections: List[PkglistSection], approver: str):
        text = "-------------------------------------------------------------------\n"
        now = datetime.datetime.utcnow()
        date = now.strftime("%a %b %d %H:%M:%S UTC %Y")
        url = makeurl(self.apiurl, ['person', approver])
        root = ET.parse(http_GET(url))
        realname = root.find('realname').text
        email = root.find('email').text
        text += f"{date} - {realname} <{email}>\n\n- Approved changes to summary-staging.txt\n"
        for section in sections:
            if section.command == PkglistSectionCommend.MOVE:
                text += self.format_move(section)
            elif section.command == PkglistSectionCommend.ADD:
                text += self.format_add(section)
            elif section.command == PkglistSectionCommend.REMOVE:
                text += self.format_remove(section)
        with open(filename + '.new', 'w') as writer:
            writer.write(text)
            with open(filename, 'r') as reader:
                for line in reader:
                    writer.write(line)
        os.rename(filename + '.new', filename)

    def check_staging_accept(self, project: str, target: str):
        comments = self.comment.get_comments(project_name=project)
        comment, _ = self.comment.comment_find(comments, MARKER)
        approver = self.is_approved(comment, comments)
        if not approver:
            return
        sections = self.parse_sections(comment['comment'])
        project, package = devel_project_get(self.apiurl, target, '000package-groups')
        if project is None or package is None:
            raise ValueError('Could not determine devel project or package for the "000package-groups"!')
        with tempfile.TemporaryDirectory() as tmpdirname:
            checkout_package(self.apiurl, project, package, expand_link=True, outdir=tmpdirname)
            self.apply_commands(tmpdirname + '/summary-staging.txt', sections)
            self.apply_changes(tmpdirname + '/package-groups.changes', sections, approver)
            package = Package(tmpdirname)
            package.commit(msg='Approved packagelist changes', skip_local_service_run=True)
