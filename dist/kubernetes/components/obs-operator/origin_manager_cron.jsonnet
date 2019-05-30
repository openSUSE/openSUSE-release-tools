local params = std.extVar("__ksonnet/params").components.origin_manager_cron;
local review_bot = import '../review_bot.libsonnet';

[
  review_bot.parts.cron.base(
    params.prefix, "cron",
    "0 0 * * 0,2,4", params.cpu, params.memory, params.image,
    "osc origin cron")
]
