#!/usr/bin/env python3
"""
Generates an HTML report of packages failing to build in openSUSE:Factory:Rebuild.
For each failing package it shows:
  - Last build date in openSUSE:Factory:Rebuild
  - Whether the package builds in openSUSE:Factory
  - The devel project
  - Open Requests targeting the devel project
"""

import sys
import os
import json
import argparse
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET

# osc imports
import osc.conf
import osc.core

from jinja2 import Environment, FileSystemLoader

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
APIURL = "https://api.opensuse.org"
REBUILD_PROJECT = "openSUSE:Factory:Rebuild"
FACTORY_PROJECT = "openSUSE:Factory"
# Repositories / arches to consider for build results
REPO = "standard"
ARCH = "x86_64"
# Cache file for devel projects data
DEVEL_CACHE_FILE = "devel_cache.json"
# Minimum request ID to consider, this should be bumped periodically because
# we only want to check recent submissions.
MIN_REQUEST_ID = 1235000

# --------------------------------------------------------------------------- #
# OBS helpers
# --------------------------------------------------------------------------- #

def obs_get(path: str, query: dict | None = None) -> ET.Element:
    """Perform a raw GET against the OBS API and return parsed XML."""
    url = osc.core.makeurl(APIURL, path.split("/"), query=query or {})
    log.debug(f"obs_get: {url}")
    try:
        f = osc.core.http_GET(url)
        return ET.parse(f).getroot()
    except Exception as e:
        log.error(f"obs_get failed for {url}: {type(e).__name__}: {e}")
        raise


def rebuild_status(project: str = REBUILD_PROJECT) -> int:
    """
    Check how far along the rebuild project is.

    Returns the percentage (0-100) of packages that have reached a final state.
    100 means the rebuild is fully complete.
    """
    root = obs_get(
        f"build/{project}/_result",
        {"view": "status", "arch": ARCH, "multibuild": "1"},
    )
    total = 0
    pending = 0
    for result_el in root.findall("result"):
        for status_el in result_el.findall("status"):
            code = status_el.get("code", "")
            if code in ("excluded"):
                continue
            total += 1
            code = status_el.get("code", "")
            if code in ("blocked", "building", "scheduled", "dispatching", "signing"):
                pending += 1
    return ((total - pending) * 100 // total) if total else 100


def get_failed_packages_with_results(project: str = REBUILD_PROJECT) -> tuple[list[str], dict[str, list[dict]]]:
    """
    Return (sorted_fail_list, {package: [results]}) from a single API call.

    Each result dict: {code, detail}
    """
    failed: set[str] = set()
    results_map: dict[str, list[dict]] = {}
    root = obs_get(
        f"build/{project}/_result",
        {"view": "status", "arch": ARCH, "multibuild": "1"},
    )

    for result_el in root.findall("result"):
        for status_el in result_el.findall("status"):
            pkg = status_el.get("package", "")
            code = status_el.get("code", "")
            detail_el = status_el.find("details")
            detail = detail_el.text if detail_el is not None else ""
            results_map.setdefault(pkg, []).append({"code": code, "detail": detail})
            if code in ("failed", "unresolvable", "broken"):
                failed.add(pkg)

    return sorted(failed), results_map


def get_all_devel_projects(project: str = FACTORY_PROJECT) -> dict[str, tuple[str, str]]:
    """
    Fetch all packages and their devel info from a project in a single API call.
    Returns dict of package_name -> (devel_project, devel_package).
    Uses a local cache file if available from the same day.
    """
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEVEL_CACHE_FILE)

    if os.path.isfile(cache_path):
        try:
            # Check if file is from previous calendar day
            mtime = os.path.getmtime(cache_path)
            if datetime.fromtimestamp(mtime, tz=timezone.utc).date() == datetime.now(tz=timezone.utc).date():
                log.info(f"Loading devel projects from cache file: {cache_path}")
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {k: tuple(v) for k, v in data.items()}
            else:
                log.info(f"Cache file {cache_path} is outdated (from previous day), recreating…")
        except Exception as e:
            log.warning(f"Failed to load cache file: {e}, fetching from API instead")


    log.info(f"Fetching devel projects for all packages in {project} …")
    try:
        root = obs_get("search/package", {"match": f'@project="{project}"'})
    except Exception as e:
        log.error(f"get_all_devel_projects failed: {type(e).__name__}: {e}")
        return {}

    result: dict[str, tuple[str, str]] = {}
    for pkg_el in root.findall("package"):
        pkg_name = pkg_el.get("name", "")
        devel = pkg_el.find("devel")
        if devel is not None:
            result[pkg_name] = (devel.get("project", ""), devel.get("package", pkg_name))
        else:
            result[pkg_name] = ("", "")

    log.info(f"Got devel info for {len(result)} packages.")

    # Save to cache file
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        log.info(f"Saved devel projects to cache file: {cache_path}")
    except Exception as e:
        log.warning(f"Failed to save cache file: {e}")

    return result


def fetch_open_requests() -> dict:
    """
    Fetch all open/under-review submit requests from OBS and return them
    indexed by target_project -> package -> list of request dicts.

    Notes:
      - XPath string literals MUST use single quotes (encoded as %27 by makeurl).
      - Double quotes get encoded as %22 and OBS drops them → HTTP 400.
    """
    match_expr = "(state/@name='new' or state/@name='review')"

    log.info(f"Fetching open requests")
    try:
        root = obs_get("search/request", {"match": match_expr})
    except Exception as e:
        log.error(f"fetch_open_requests failed: {type(e).__name__}: {e}")
        return {}

    if root.tag == "request":
        request_els = [root]
    else:
        request_els = root.findall("request")

    index: dict[str, dict[str, list[dict]]] = {}

    SKIP_ACTION_TYPES = {"change_devel", "maintenance_incident", "maintenance_release", "add_role", "set_bugowner"}

    for req_el in request_els:
        rid = req_el.get("id", "")
        if int(rid) < MIN_REQUEST_ID:
            continue

        state_el = req_el.find("state")
        if state_el is None:
            log.warning(f"request id={rid!r} has no <state> — skipping")
            continue
        state_name = state_el.get("name", "")
        state_who  = state_el.get("who", "")

        for action_el in req_el.findall("action"):
            action_type = action_el.get("type", "")
            if action_type in SKIP_ACTION_TYPES:
                continue

            target_el = action_el.find("target")
            if target_el is None:
                log.warning(f"request id={rid!r} has an <action> with no <target> — skipping")
                continue

            tgt_prj = target_el.get("project", "")
            tgt_pkg = target_el.get("package", "")
            if not tgt_pkg:
                continue

            record = {
                "id":             rid,
                "type":           action_type,
                "state":          state_name,
                "creator":        state_who,
                "target_project": tgt_prj,
                "target_package": tgt_pkg,
            }
            index.setdefault(tgt_prj, {}).setdefault(tgt_pkg, []).append(record)

    log.info(f"Read {len(index)} projects, with {sum(len(pkgs) for pkgs in index.values())} total requests")

    return index


def fetch_package_history(pkg: str) -> tuple[str, datetime | None]:
    """Helper to fetch history for a single package."""
    try:
        root = obs_get(f"build/{REBUILD_PROJECT}/{REPO}/{ARCH}/{pkg}/_history")
        entries = root.findall("entry")
        if entries:
            t = entries[-1].get("time")
            if t:
                return pkg, datetime.fromtimestamp(int(t), tz=timezone.utc)
    except Exception:
        pass
    return pkg, None


def collect_data(limit: int | None = None) -> list[dict]:
    """Collect all required data and return a list of package records."""
    log.info("Fetching failing packages from %s …", REBUILD_PROJECT)
    packages, rebuild_results_map = get_failed_packages_with_results(REBUILD_PROJECT)
    _, rebuild_results_map_f = get_failed_packages_with_results(FACTORY_PROJECT)

    if limit:
        packages = packages[:limit]
    log.info("Found %d failing packages.", len(packages))

    sr_cache = fetch_open_requests()
    devel_cache = get_all_devel_projects(FACTORY_PROJECT)

    log.info("Fetching build histories in parallel...")
    with ThreadPoolExecutor() as executor:
        history_map = dict(executor.map(fetch_package_history, packages))

    records = []
    for i, pkg in enumerate(packages, 1):
        log.info("[%d/%d] Processing %s …", i, len(packages), pkg)

        rebuild_results = rebuild_results_map.get(pkg, [])
        factory_results = rebuild_results_map_f.get(pkg, [])
        factory_result = factory_results[0] if factory_results else {"code": "unknown", "detail": ""}

        last_build = history_map.get(pkg)

        base_name = pkg.split(":", 1)[0]
        devel_project, devel_package = devel_cache.get(base_name, ("", ""))

        records.append(
            {
                "package": pkg,
                "last_build": last_build,
                "last_build_ts": int(last_build.timestamp()) if last_build else None,
                "build_results": rebuild_results,
                "factory_status": factory_result["code"],
                "factory_detail": factory_result["detail"],
                "devel_project": devel_project,
                "devel_package": devel_package,
                "open_requests": [],
            }
        )

    # Match open requests to packages using a lookup dict.
    # Key by base name (multibuild suffix stripped) so that all multibuild
    # variants (e.g. foo:a and foo:b) receive the same open requests.
    rec_index: dict[str, list[dict]] = {}
    for rec in records:
        rec_index.setdefault(rec["package"].split(":", 1)[0], []).append(rec)

    for project, pkgs in sr_cache.items():
        for pkg_name, srs in pkgs.items():
            for rec in rec_index.get(pkg_name, []):
                rec["open_requests"].extend(srs)

    return records


def render_html(records: list[dict], output_path: str, rebuild_pct: int) -> None:

    # Get the path from where the script is run and look for templates in the same directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(script_dir, 'templates')
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
    template = env.get_template("ftbfs.html")
    now = datetime.now(tz=timezone.utc)
    html = template.render(
        packages=records,
        rebuild_project=REBUILD_PROJECT,
        factory_project=FACTORY_PROJECT,
        generated_at_utc=now.strftime("%Y-%m-%d %H:%M UTC"),
        generated_at_iso=now.isoformat(),
        rebuild_pct=rebuild_pct,
        )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(
        description="Generate FTBFS HTML report for openSUSE:Factory:Rebuild"
    )
    parser.add_argument(
        "-o", "--output",
        default="ftbfs.html",
        help="Output HTML file path (default: ftbfs.html)",
    )
    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=None,
        help="Limit the number of packages processed (for testing)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "-i", "--incomplete",
        nargs="?",
        type=int,
        const=0,
        help="Generate report even if the rebuild is still in progress. Optionally specify minimum completion percentage (0-100).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load osc configuration (reads ~/.config/osc/oscrc)
    try:
        osc.conf.get_config()
    except osc.conf.ConfigError as e:
        log.error("Could not load osc configuration: %s", e)
        sys.exit(1)

    rebuild_pct = rebuild_status()
    log.info("Rebuild completion: %d%%", rebuild_pct)
    if args.incomplete is None:
        if rebuild_pct < 100:
            log.error(
                "%s still has pending builds – %.1f%% complete. "
                "Use --incomplete to generate the report anyway.",
                REBUILD_PROJECT, rebuild_pct,
            )
            sys.exit(1)
    elif rebuild_pct < args.incomplete:
        log.error(
            "%s still has pending builds – %.1f%% complete. "
            "The report will only be generated if completion is >= %d%%.",
            REBUILD_PROJECT, rebuild_pct, args.incomplete,
        )
        sys.exit(1)

    records = collect_data(limit=args.limit)
    render_html(records, args.output, rebuild_pct)
    print(f"\nReport saved to: {args.output} with a total of {len(records)} packages listed")


if __name__ == "__main__":
    main()
