"""
This module performs actions related to the staging summary and the comment that these actions are based on.
"""

import datetime
import textwrap
import re
import tempfile
import logging
import os
import sys
from typing import Dict, List, Union, Optional
from lxml import etree as ET

from osclib.comments import CommentAPI
from osc.core import checkout_package, http_GET, makeurl
from osc.core import Package

MARKER = 'PackageListDiff'


class PkglistComments(object):
    """Handling staging comments of diffs"""

    def __init__(self, apiurl):
        self.apiurl = apiurl
        self.comment = CommentAPI(apiurl)

    def read_summary_file(self, file: str) -> Dict[str, List[str]]:
        """
        Read the summary file and parses the result into a dict.

        :param file: The path to the summary file to read.
        :returns: A dict following the format {"packagename": ["group1", "group2"]}.
        """
        ret = dict()
        with open(file, 'r') as f:
            for line in f:
                pkg, group = line.strip().split(':')
                ret.setdefault(pkg, [])
                ret[pkg].append(group)
        return ret

    def write_summary_file(self, file: str, content: dict):
        """
        Write the summary file to disk in the desired format.

        :param file: The path to the summary file to write.
        :param content: The dict in the format of {"packagename": ["group1", "group2"]}.
        """
        output = []
        for pkg in sorted(content):
            for group in sorted(content[pkg]):
                output.append(f"{pkg}:{group}")

        with open(file, 'w') as f:
            for line in sorted(output):
                f.write(line + '\n')

    def calculcate_package_diff(self, old_file: str, new_file: str):
        """
        Reads the two summary files from disk and calculates the diff between the two dictionaries and generates a
        report for the calculated diff.

        :param old_file: The path to the old summary file.
        :param new_file: The path to the new summary file.
        """
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
        """
        Checks a given project for the difference between two summary files. If there is a diff then a comment to the
        project is added. This method will also handle deleting a redundant comment.

        :param project: The project to handle the package diff for.
        :param old_file: The path to the file with the old package state
        :param new_file: The path to the file with the new package state.
        """
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
        """
        Check the comments of a project for approval of the changes that the bot would perform.

        :param comment: The comment that the bot made.
        :param comments: The comments of the target project.
        :returns: None or the username of the person
        """
        if not comment:
            return None

        for c in comments.values():
            if c['parent'] == comment['id']:
                ct = c['comment']
                if ct.startswith('approve ') or ct == 'approve':
                    return c['who']
        return None

    def parse_title(self, line: str) -> Optional[Dict[str, Union[str, List[str]]]]:
        """
        Parses the header of a section from a single line of a comment that has been passed.

        :param line: The line that should be checked.
        :returns: None or a dict with the following structure: {"cmd": "<add|move|remove", "<to|from>": str, "pkgs": []}
        """
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

    def parse_sections(self, comment: str) -> List[Dict[str, Union[str, List[str]]]]:
        """
        Parses the comment in the staging project that should be used to generate the staging-summary and the changelog
        file.

        :param comment: The text of the comment that should be parsed.
        :returns: A list of dictionaries witht the changes parsed from the given comment.
        """
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

    def apply_move(self, content: Dict[str, List[str]], section: Dict[str, Union[str, List[str]]]):
        """
        Performs a transformation of the parsed comment to the summary file structure.

        :param content: The dict that contains the summary file content.
        :param section: The section that contains a move operation of a package
        """
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

    def apply_add(self, content: Dict[str, List[str]], section: Dict[str, Union[str, List[str]]]):
        """
        Performs a transformation of the parsed comment to the summary file structure.

        :param content: The dict that contains the summary file content.
        :param section: The section that contains an add operation of a package
        """
        for pkg in section['pkgs']:
            content.setdefault(pkg, [])
            content[pkg] += section['to']

    def apply_remove(self, content: Dict[str, List[str]], section: Dict[str, Union[str, List[str]]]):
        """
        Performs a transformation of the parsed comment to the summary file structure.

        :param content: The dict that contains the summary file content.
        :param section: The section that contains a remove operation of a package
        """
        for pkg in section['pkgs']:
            pkg_content = content[pkg]
            for group in section['from']:
                try:
                    pkg_content.remove(group)
                except ValueError:
                    logging.error(f"Can't remove {pkg} from {group}, not there. Mismatch.")
                    sys.exit(1)
            content[pkg] = pkg_content

    def apply_commands(self, filename: str, sections: List[Dict[str, Union[str, List[str]]]]):
        """
        Updates the summary file with the sections parsed from the comment that was found.

        :param filename: The location of the summary file that should be updated.
        :param sections: The list of dicts that represents the sections parsed from the comment.
        """
        content = self.read_summary_file(filename)
        for section in sections:
            if section['cmd'] == 'move':
                self.apply_move(content, section)
            elif section['cmd'] == 'add':
                self.apply_add(content, section)
            elif section['cmd'] == 'remove':
                self.apply_remove(content, section)
        self.write_summary_file(filename, content)

    def format_pkgs(self, pkgs):
        text = ', '.join(pkgs)
        return "  " + "\n  ".join(textwrap.wrap(text, width=68, break_long_words=False, break_on_hyphens=False)) + "\n\n"

    def format_move(self, section):
        gfrom = ','.join(section['from'])
        gto = ','.join(section['to'])
        text = f"  * Move from {gfrom} to {gto}:\n"
        return text + self.format_pkgs(section['pkgs'])

    def format_add(self, section):
        gto = ','.join(section['to'])
        text = f"  * Add to {gto}:\n"
        return text + self.format_pkgs(section['pkgs'])

    def format_remove(self, section):
        gfrom = ','.join(section['from'])
        text = f"  * Remove from {gfrom}:\n"
        return text + self.format_pkgs(section['pkgs'])

    def apply_changes(self, filename: str, sections: List[Dict[str, Union[str, List[str]]]], approver: str):
        """
        Generates the changelog entry for the .changes file

        :param filename: The path to the changes file
        :param sections: The sections that should be generated. Can be "move", "add" or "remove"
        :param approver: The OBS account that should be used for the changelog entry header
        """
        text = "-------------------------------------------------------------------\n"
        now = datetime.datetime.utcnow()
        date = now.strftime("%a %b %d %H:%M:%S UTC %Y")
        url = makeurl(self.apiurl, ['person', approver])
        root = ET.parse(http_GET(url))
        realname = root.find('realname').text
        email = root.find('email').text
        text += f"{date} - {realname} <{email}>\n\n- Approved changes to summary-staging.txt\n"
        for section in sections:
            if section['cmd'] == 'move':
                text += self.format_move(section)
            elif section['cmd'] == 'add':
                text += self.format_add(section)
            elif section['cmd'] == 'remove':
                text += self.format_remove(section)
        with open(filename + '.new', 'w') as writer:
            writer.write(text)
            with open(filename, 'r') as reader:
                for line in reader:
                    writer.write(line)
        os.rename(filename + '.new', filename)

    def check_staging_accept(self, project: str, target: str):
        """
        Validated that someone approved the submission of the source to the target and then manipulates the staging
        summary and package group changelog according to the approved changes.

        :param project: The project that should be checked before submission to target.
        :param target: The target project that should be used for manipulation of the package groups.
        """
        comments = self.comment.get_comments(project_name=project)
        comment, _ = self.comment.comment_find(comments, MARKER)
        approver = self.is_approved(comment, comments)
        if not approver:
            return
        sections = self.parse_sections(comment['comment'])
        with tempfile.TemporaryDirectory() as tmpdirname:
            checkout_package(self.apiurl, target, '000package-groups', expand_link=True, outdir=tmpdirname)
            # Now should go to Attribute OSRT:summary-staging in the package or project
            self.apply_commands(tmpdirname + '/summary-staging.txt', sections)
            # Should now go to a devel project - https://build.suse.de/project/show/Devel:ReleaseManagement:SLE-15-SP5
            self.apply_changes(tmpdirname + '/package-groups.changes', sections, approver)
            package = Package(tmpdirname)
            package.commit(msg='Approved packagelist changes', skip_local_service_run=True)
