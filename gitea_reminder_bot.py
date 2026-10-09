#!/usr/bin/python3
import sys
import os
import re
import datetime
import ReviewBot


class ReviewBotImpl(ReviewBot.ReviewBot):
    """
    Reminder bot for src.suse.de (Gitea).

    Policy:
      - FPR (products/*): review reminders -> by default ping humans (missing individual reviewers)
        Optionally can ping groups via BROKIN_REVIEW_PING_USERS.

      - PR (pool/*): review reminders -> INDIVIDUAL missing reviewers
        Build reminders -> YES (either direct ObsStaging RED, or forwarded from FPR)

    Anti-spam:
      - Each reminder type has its own marker; bot posts once per PR per marker.

    Env knobs:
      BROKIN_CROSS_POST_REFS=0         Disable cross-post to referenced PRs (default enabled)
      BROKIN_FORWARDING_BOTS=...       Comma-separated forwarding bot usernames (default: autogits_workflow_pr_bot)

      BROKIN_REVIEW_REMINDER=1         Enable review reminders (default enabled)

      # OPTIONAL: if set, FPR reminders will ping these accounts instead of humans
      BROKIN_REVIEW_PING_USERS="sle-release-manager-review,sle-staging-manager-review,autobuild-review"

      # Label gating:
      BROKIN_REMINDER_LABEL="staging/In Progress"   Only remind if this label is present
      BROKIN_REMINDER_DAYS=4                        Only remind if label was applied >= N days ago

      # Skip accounts matching regex (bots or group accounts)
      BROKIN_SKIP_REVIEWERS_RE="(^autogits_|_bot$|-review$)"
    """

    DEFAULT_FORWARDING_BOTS = {"autogits_workflow_pr_bot"}

    PR_REF_SHORT_RE = re.compile(r"PR:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*!\s*([0-9]+)")
    PR_REF_URL_RE = re.compile(r"PR:\s*https?://[^/]+/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pulls/([0-9]+)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.request_default_return = None

        self.build_marker = "[OBS-STAGING-REMINDER]"
        self.review_marker = "[REVIEW-REMINDER]"
        self.legacy_markers = {"[BROKIN-BUILD-REMINDER]"}

        env_bots = os.environ.get("BROKIN_FORWARDING_BOTS", "").strip()
        if env_bots:
            self.forwarding_bots = {b.strip() for b in env_bots.split(",") if b.strip()}
        else:
            self.forwarding_bots = set(self.DEFAULT_FORWARDING_BOTS)

        self.cross_post_refs = os.environ.get("BROKIN_CROSS_POST_REFS", "1").strip() != "0"

        self.review_reminder_enabled = os.environ.get("BROKIN_REVIEW_REMINDER", "1").strip() != "0"

        ping_users = os.environ.get("BROKIN_REVIEW_PING_USERS", "").strip()
        self.review_ping_users = [u.strip() for u in ping_users.split(",") if u.strip()] if ping_users else []

        # Label gating
        self.required_label = os.environ.get("BROKIN_REMINDER_LABEL", "").strip() or None
        self.reminder_days = int(os.environ.get("BROKIN_REMINDER_DAYS", "0").strip() or "0")

        skip_re = os.environ.get("BROKIN_SKIP_REVIEWERS_RE", r"(^autogits_|_bot$|-review$)").strip()
        self.skip_reviewers_re = re.compile(skip_re) if skip_re else None

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        req = getattr(self, "request", None)
        owner = target_project
        repo = target_package
        sha = src_rev

        self.logger.info(f"Checking {src_project}/{src_package} ({sha}) -> {owner}/{repo}")

        # ---- A) Review reminder ----
        if self.review_reminder_enabled and req is not None:
            self._maybe_post_review_reminder(req)

        # ---- B) Build reminder (ObsStaging only) ----
        state, status_url = self._obs_staging_status(owner=owner, repo=repo, commit_sha=sha)

        if state is None:
            self.logger.info(f"No final ObsStaging status for {owner}/{repo}@{sha}, ignoring build reminder")
            return None

        st = str(state).lower()

        if st == "success":
            self.logger.info(f"ObsStaging is green for {owner}/{repo}@{sha} ({status_url})")
            return None

        if st in ("failure", "error", "failed", "cancelled", "canceled"):
            self.logger.info(f"ObsStaging is RED for {owner}/{repo}@{sha} (state={st}) — build: {status_url}")
            if req is None:
                self.logger.warning("No request object available; cannot comment.")
                return None

            self._comment_build_failure_on_request(req=req, state=st, status_url=status_url)
            return None

        self.logger.info(f"ObsStaging non-final/unknown state='{st}' for {owner}/{repo}@{sha}; ignoring build reminder")
        return None

    # ---------------------------
    # Label gating helpers
    # ---------------------------

    def _fetch_issue_labels(self, owner: str, repo: str, pr_id: int) -> set[str]:
        """Fetch current labels on the PR (issues API)."""
        api = self.platform.api
        r = api.get(f"repos/{owner}/{repo}/issues/{pr_id}/labels")
        if r.status_code == 404:
            return set()
        r.raise_for_status()
        data = r.json() or []
        out = set()
        for x in data:
            name = (x or {}).get("name")
            if name:
                out.add(name)
        return out

    def _fetch_label_applied_at(self, owner: str, repo: str, pr_id: int, label_name: str):
        """
        Try to determine when a label was applied.

        Prefer timeline because "labels list" usually doesn't contain "applied_at".
        If timeline isn't available or doesn't show it, return None.
        """
        api = self.platform.api
        r = api.get(f"repos/{owner}/{repo}/issues/{pr_id}/timeline")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        items = r.json() or []

        # We want the most recent time it was applied (or first time, depends; we use earliest apply)
        applied_times = []

        for it in items:
            # Gitea timeline items vary. We'll handle common shapes.
            t = (it or {}).get("type") or ""
            created_at = (it or {}).get("created_at") or (it or {}).get("createdAt")

            # Sometimes there is "label" field, sometimes "labels".
            lbl = (it or {}).get("label")
            lbls = (it or {}).get("labels")

            match = False
            if isinstance(lbl, dict) and (lbl.get("name") == label_name):
                match = True
            elif isinstance(lbls, list):
                for one in lbls:
                    if isinstance(one, dict) and one.get("name") == label_name:
                        match = True
                        break

            # Accept timeline events that represent labeling.
            if match and created_at:
                try:
                    dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    applied_times.append(dt)
                except Exception:
                    pass

        if not applied_times:
            return None

        # Use earliest time we saw it applied (safer for ">= days" gating).
        return min(applied_times)

    def _label_old_enough(self, applied_at) -> bool:
        """If we can't determine applied time, allow reminder (conservative)."""
        if not self.reminder_days or self.reminder_days <= 0:
            return True
        if applied_at is None:
            # Can't determine applied date -> don't block reminders forever.
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        age = now - applied_at
        return age.days >= self.reminder_days

    # ---------------------------
    # Build reminder implementation
    # ---------------------------

    def _obs_staging_status(self, owner: str, repo: str, commit_sha: str):
        api = self.platform.api
        self.logger.info(f"Looking up commit status for {owner}/{repo}@{commit_sha}")

        resp = api.get(f"repos/{owner}/{repo}/commits/{commit_sha}/status")
        if resp.status_code == 404:
            self.logger.info(f"No commit status found for {owner}/{repo}@{commit_sha}")
            return None, None

        resp.raise_for_status()
        data = resp.json() or {}

        if not isinstance(data, dict):
            self.logger.warning(f"Unexpected commit status payload type: {type(data)}")
            return None, None

        combined_state = data.get("state")
        statuses = data.get("statuses") or []
        if statuses is None:
            statuses = []

        obs_status = None
        for s in statuses:
            ctx = (s.get("context") or "") + " " + (s.get("description") or "")
            if "ObsStaging" in ctx:
                obs_status = s
                break

        if obs_status is not None:
            state = obs_status.get("status") or obs_status.get("state")
            target_url = obs_status.get("target_url") or obs_status.get("targetUrl") or obs_status.get("url")
            self.logger.info(f"ObsStaging status for {owner}/{repo}@{commit_sha}: {state} ({target_url})")
            return state, target_url

        self.logger.info(f"No explicit ObsStaging status, using combined state={combined_state}")
        if combined_state in ("success", "failure", "error"):
            any_url = None
            if statuses:
                any_url = statuses[0].get("target_url") or statuses[0].get("targetUrl") or statuses[0].get("url")
            return combined_state, any_url

        return None, None

    def _is_fpr_request(self, req) -> bool:
        creator = getattr(req, "creator", "") or ""
        desc = getattr(req, "description", "") or ""

        if creator in self.forwarding_bots:
            return True

        lowered = desc.lower()
        if "forwarded pull request" in lowered:
            return True

        if self.PR_REF_SHORT_RE.search(desc) or self.PR_REF_URL_RE.search(desc):
            return True

        return False

    def _comment_build_failure_on_request(self, req, state: str, status_url: str | None):
        owner = getattr(req, "_owner", None)
        repo = getattr(req, "_repo", None)
        pr_id = getattr(req, "_pr_id", None)
        creator = getattr(req, "creator", None)
        reqid = getattr(req, "reqid", None)
        description = getattr(req, "description", "") or ""

        if not owner or not repo or not pr_id:
            self.logger.warning(f"Missing owner/repo/pr_id on request: {reqid} — cannot comment.")
            return

        is_fpr = self._is_fpr_request(req)

        if is_fpr:
            self.logger.info(f"Skipping build reminder on FPR itself: {reqid}")
            if self.cross_post_refs:
                refs = self._parse_referenced_prs(description)
                for ref_owner, ref_repo, ref_pr in refs:
                    ref_reqid = f"{ref_owner}:{ref_repo}:{ref_pr}"
                    self._maybe_comment_build_failure(
                        owner=ref_owner,
                        repo=ref_repo,
                        pr_id=ref_pr,
                        creator=None,
                        description="",
                        reqid=ref_reqid,
                        state=state,
                        status_url=status_url,
                        is_cross_post=True,
                        original_reqid=reqid,
                    )
            return

        self._maybe_comment_build_failure(
            owner=owner,
            repo=repo,
            pr_id=pr_id,
            creator=creator,
            description=description,
            reqid=reqid,
            state=state,
            status_url=status_url,
            is_cross_post=False,
            original_reqid=None,
        )

    def _maybe_comment_build_failure(
        self,
        owner: str,
        repo: str,
        pr_id: int,
        creator: str | None,
        description: str,
        reqid: str,
        state: str,
        status_url: str | None,
        is_cross_post: bool,
        original_reqid: str | None,
    ):
        comments_path = f"repos/{owner}/{repo}/issues/{pr_id}/comments"

        markers = {self.build_marker, *self.legacy_markers}

        if self._has_any_marker(comments_path, markers, reqid):
            self.logger.info(f"Build reminder already posted for {reqid}; skipping comment.")
            return

        mentions = []
        if creator:
            mentions = self._determine_mentions(creator=creator, description=description)

        if is_cross_post:
            author = self._fetch_pr_author(owner, repo, pr_id)
            if author and author not in mentions:
                mentions.insert(0, author)

        mention_line = " ".join(f"@{m}" for m in mentions).strip()
        build_line = f"FPR/OBS: {status_url}" if status_url else "FPR/OBS: (link not available)"
        cross_line = ""
        if is_cross_post and original_reqid:
            cross_line = f"(Forwarded reminder from `{original_reqid}`)\n"

        msg = (
            f"{self.build_marker}\n\n"
            f"{mention_line}\n"
            f"{cross_line}"
            f"This PR currently **does not build** in ObsStaging (state: `{state}`).\n\n"
            f"{build_line}\n"
        ).strip() + "\n"

        self._post_comment(reqid=reqid, comments_path=comments_path, msg=msg)

    # ---------------------------
    # Review reminder implementation
    # ---------------------------

    def _maybe_post_review_reminder(self, req):
        owner = getattr(req, "_owner", None)
        repo = getattr(req, "_repo", None)
        pr_id = getattr(req, "_pr_id", None)
        reqid = getattr(req, "reqid", None)

        if not owner or not repo or not pr_id:
            return

        # ---- LABEL GATING HERE ----
        if self.required_label:
            labels = self._fetch_issue_labels(owner, repo, pr_id)
            if self.required_label not in labels:
                self.logger.info(
                    f"Skipping review reminder for {reqid}: missing label '{self.required_label}' (labels={labels})"
                )
                return

            applied_at = self._fetch_label_applied_at(owner, repo, pr_id, self.required_label)
            if not self._label_old_enough(applied_at):
                self.logger.info(
                    f"Skipping review reminder for {reqid}: label '{self.required_label}' not old enough yet"
                )
                return

        comments_path = f"repos/{owner}/{repo}/issues/{pr_id}/comments"

        if self._has_any_marker(comments_path, {self.review_marker}, reqid):
            self.logger.info(f"Review reminder already posted for {reqid}; skipping.")
            return

        reviewers, teams = self._fetch_requested_reviewers_and_teams(owner, repo, pr_id)
        self.logger.info(f"Requested reviewers for {owner}/{repo}!{pr_id}: {reviewers} (teams={teams})")

        approved = self._fetch_approved_reviewers(owner, repo, pr_id)
        missing = [r for r in reviewers if r not in approved]

        # If nothing requested at all, nothing to do
        if not reviewers and not teams and not self.review_ping_users:
            return

        is_fpr = self._is_fpr_request(req)

        mentions = []

        if self.review_ping_users:
            # If user explicitly configured ping-users, use them (even on FPRs)
            mentions = list(self.review_ping_users)
        else:
            # Otherwise always ping missing individuals (also for FPRs),
            # but filter bots and group accounts.
            for r in missing:
                if not r:
                    continue
                if self.skip_reviewers_re and self.skip_reviewers_re.search(r):
                    continue
                mentions.append(r)

        # De-dupe
        seen = set()
        mentions = [m for m in mentions if not (m in seen or seen.add(m))]

        if not mentions:
            return

        mention_line = " ".join(f"@{m}" for m in mentions).strip()
        pr_url = f"https://src.suse.de/{owner}/{repo}/pulls/{pr_id}"

        details = ""
        if reviewers:
            missing_txt = ", ".join(f"`{m}`" for m in missing) if missing else "(none)"
            details = f"Missing approval from: {missing_txt}\n\n"

        team_line = ""
        if teams:
            team_line = "Requested review team(s): " + ", ".join(f"`{t}`" for t in teams)

        msg = (
            f"{self.review_marker}\n\n"
            f"{mention_line}\n"
            f"Review is still requested for this PR.\n\n"
            f"{details}"
            f"{team_line}\n"
            f"PR: {pr_url}\n"
        ).strip() + "\n"

        self._post_comment(reqid=reqid, comments_path=comments_path, msg=msg)

    def _fetch_approved_reviewers(self, owner: str, repo: str, pr_id: int):
        api = self.platform.api
        try:
            r = api.get(f"repos/{owner}/{repo}/pulls/{pr_id}/reviews")
            if r.status_code == 404:
                return []
            r.raise_for_status()
            reviews = r.json() or []
        except Exception as e:
            self.logger.warning(f"Failed to fetch reviews for {owner}/{repo}!{pr_id}: {e!r}")
            return []

        approved = []
        for rev in reviews:
            if rev.get("dismissed", False):
                continue
            state = (rev.get("state") or "").upper()
            user = (rev.get("user") or {}).get("login")
            if user and state == "APPROVED":
                approved.append(user)

        seen = set()
        uniq = []
        for u in approved:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    # ---------------------------
    # Helpers
    # ---------------------------

    def _has_any_marker(self, comments_path: str, markers: set[str], reqid: str | None):
        api = self.platform.api
        try:
            r = api.get(comments_path)
            r.raise_for_status()
            existing = r.json() or []
        except Exception as e:
            self.logger.warning(f"Failed to fetch existing comments for {reqid}: {e!r}")
            return False

        for c in existing:
            body = (c.get("body") or "")
            for m in markers:
                if m in body:
                    return True
        return False

    def _post_comment(self, reqid: str, comments_path: str, msg: str):
        api = self.platform.api
        if getattr(self, "dryrun", False):
            self.logger.info(f"(dryrun) would comment on {reqid}:\n{msg}")
            return

        try:
            pr = api.post(comments_path, json={"body": msg})
            pr.raise_for_status()
            self.logger.info(f"Posted comment on {reqid}.")
        except Exception as e:
            self.logger.error(f"Failed to post comment on {reqid}: {e!r}")

    def _parse_referenced_prs(self, description: str):
        refs = []

        for m in self.PR_REF_SHORT_RE.finditer(description or ""):
            path = m.group(1)
            pr_id = int(m.group(2))
            o, r = path.split("/", 1)
            refs.append((o, r, pr_id))

        for m in self.PR_REF_URL_RE.finditer(description or ""):
            o = m.group(1)
            r = m.group(2)
            pr_id = int(m.group(3))
            refs.append((o, r, pr_id))

        seen = set()
        unique = []
        for x in refs:
            if x not in seen:
                seen.add(x)
                unique.append(x)
        return unique

    def _determine_mentions(self, creator: str | None, description: str):
        if not creator:
            return []

        if creator not in self.forwarding_bots:
            return [creator]

        refs = self._parse_referenced_prs(description)
        if not refs:
            return [creator]

        authors = []
        for o, r, pr_id in refs:
            a = self._fetch_pr_author(o, r, pr_id)
            if a:
                authors.append(a)

        seen = set()
        unique = []
        for a in authors:
            if a not in seen:
                seen.add(a)
                unique.append(a)

        return unique if unique else [creator]

    def _fetch_pr_author(self, owner: str, repo: str, pr_id: int):
        api = self.platform.api
        try:
            r = api.get(f"repos/{owner}/{repo}/pulls/{pr_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json() or {}
            user = data.get("user") or {}
            return user.get("login")
        except Exception as e:
            self.logger.warning(f"Failed to fetch PR author {owner}/{repo}!{pr_id}: {e!r}")
            return None

    def _fetch_requested_reviewers_and_teams(self, owner: str, repo: str, pr_id: int):
        api = self.platform.api

        def parse_users_and_teams(payload):
            users = payload.get("users") or payload.get("requested_reviewers") or []
            teams = payload.get("teams") or payload.get("requested_teams") or []

            user_logins = []
            for u in users:
                login = (u or {}).get("login")
                if login:
                    user_logins.append(login)

            team_names = []
            for t in teams:
                name = (t or {}).get("name")
                org = (t or {}).get("organization") or {}
                org_name = org.get("username") or org.get("name")
                if name and org_name:
                    team_names.append(f"{org_name}/{name}")
                elif name:
                    team_names.append(name)

            return user_logins, team_names

        try:
            r = api.get(f"repos/{owner}/{repo}/pulls/{pr_id}/requested_reviewers")
            if r.status_code != 404:
                r.raise_for_status()
                data = r.json() or {}
                users, teams = parse_users_and_teams(data)
                if users or teams:
                    return users, teams
        except Exception as e:
            self.logger.warning(f"Requested reviewers endpoint failed for {owner}/{repo}!{pr_id}: {e!r}")

        try:
            r = api.get(f"repos/{owner}/{repo}/pulls/{pr_id}")
            if r.status_code == 404:
                return [], []
            r.raise_for_status()
            pr = r.json() or {}
            users, teams = parse_users_and_teams(pr)
            return users, teams
        except Exception as e:
            self.logger.warning(f"Reviewer fallback failed for {owner}/{repo}!{pr_id}: {e!r}")
            return [], []


class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = ReviewBotImpl


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
