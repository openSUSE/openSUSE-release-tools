local params = std.extVar("__ksonnet/params").components.review;
local review_bot = import '../review_bot.libsonnet';

[
  review_bot.parts.cache.base(
    params.prefix, params.cache),

  review_bot.parts.cron.base(
    params.prefix, "review",
    "*/5 * * * *", params.cpu, params.memory, params.image,
    "osrt-repo_checker --debug review"),
]
