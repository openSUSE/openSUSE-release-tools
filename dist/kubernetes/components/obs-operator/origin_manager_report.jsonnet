local params = std.extVar("__ksonnet/params").components.origin_manager_report;
local review_bot = import '../review_bot.libsonnet';

[
  review_bot.parts.cron.base(
    params.prefix, "report-" + std.asciiLower(std.strReplace(project, ":", "-")),
    "0 0 * * 0,2,4", params.cpu, params.memory, params.image,
    "osc origin -p '" + project + "' report --force-refresh")
  for project in params.projects
]
