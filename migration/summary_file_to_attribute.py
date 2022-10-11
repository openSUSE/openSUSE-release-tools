#!/bin/python3

"""
This script should migrate summary-staging.txt files to the new OSRT:StagingSummary attribute.
"""

import argparse
import json
import pathlib
import sys
from urllib.error import HTTPError

from osclib import core

# CLI script with one or more projects as an argument
parser = argparse.ArgumentParser(
    description="This script should migrate summary-staging.txt files to the new OSRT:StagingSummary attribute."
)
parser.add_argument(
    "--apiurl",
    "-A",
    dest="apiurl",
    action="store_const",
    help="URL of the API from the Open Build Service instance.",
    type=str,
)
parser.add_argument(
    "--project",
    "-p",
    dest="projects",
    action="store_const",
    help="Whitespace delimited list of projects.",
    type=str,
)
parser.add_argument(
    "--export-summary",
    "-e",
    dest="export_summary",
    action="store_const",
    help="In case you want the summary at the end exported as a JSON add this flag.",
    type=pathlib.Path,
)

project_key_valid = "valid"
project_key_not_existing = "missing"
project_key_missing_package_groups = "missing_package_groups"
project_key_missing_summary_staging = "missing_summary_staging"
project_key_success = "success"
projects = {
    project_key_valid: [],
    project_key_not_existing: [],
    project_key_missing_package_groups: [],
    project_key_missing_summary_staging: [],
    project_key_success: [],
}
summary_files_content = {}

# Parse projects and check their existence
args = parser.parse_args()
project_list = args.projects.split()

for project in project_list:
    if core.entity_exists(args.apiurl, project):
        projects[project_key_valid].append(project)
    else:
        projects[project_key_not_existing].append(project)

# Check for existence of 000package-groups package
for project in projects[project_key_valid]:
    if not core.entity_exists(args.apiurl, project, "000package-groups"):
        projects[project_key_missing_package_groups].append(project)

for project in projects[project_key_missing_package_groups]:
    project[project_key_valid].remove(project)

# Check for existence of summary-staging.txt
for project in projects[project_key_valid]:
    summary_staging_txt = core.source_file_load(
        args.apiurl, project, "000package-groups", "summary-staging.txt"
    )
    if summary_staging_txt is None:
        projects[project_key_missing_summary_staging].append(project)

for project in projects[project_key_missing_summary_staging]:
    project[project_key_valid].remove(project)

attribute_exists = True
for project, content in projects[project_key_valid]:
    try:
        core.attribute_value_save(args.apiurl, project, "StagingSummary", content)
        projects[project_key_success].append(project)
    except HTTPError as e:
        if e.code == 404:
            # Attribute doesn't exist, thus we don't need to save all non-existent attributes
            attribute_exists = False
            break
        raise e

# Print summary
print("Summary of the execution:")
print("")
print(f"\tThe script recognized the following projects: {', '.join(project_list)}")
print(f"\tThe following projects were valid: {', '.join(projects[project_key_valid])}")
print(
    f"\tThe following projects were missing: {', '.join(projects[project_key_not_existing])}"
)
print(
    "\tThe following projects were missing the 000package-groups: "
    f"{', '.join(projects[project_key_missing_package_groups])}"
)
print(
    "\tThe following projects were missing the staging summary: "
    f"{', '.join(projects[project_key_missing_summary_staging])}"
)
print(
    f"\tThe following projects were successfully converted: {', '.join(projects[project_key_success])}"
)

if args.export_summary:
    if not args.export_summary.parent.exists():
        print("Directory to save JSON in did not exist.")
        sys.exit(1)
    with open(args.export_summary, mode="wt", encoding="utf-8") as json_fd:
        json_fd.write(json.dumps(projects, indent=2))
    print("")
    print(f'Above summary can be found addtionally at: "{args.export_summary}"')
