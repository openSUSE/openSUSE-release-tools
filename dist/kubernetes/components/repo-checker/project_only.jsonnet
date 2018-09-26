local params = std.extVar("__ksonnet/params").components.project_only;
local review_bot = import '../review_bot.libsonnet';

[
  review_bot.parts.cron.base(
    params.prefix, "project-only-" + std.asciiLower(std.strReplace(project, ":", "-")),
    "0 * * * *", params.cpu, params.memory, params.image,
    "osrt-repo_checker --debug project_only '" + project + "'")
  for project in params.projects
]
