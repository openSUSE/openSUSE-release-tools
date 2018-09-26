local params = std.extVar("__ksonnet/params").components.review;
local review_bot = import '../review_bot.libsonnet';

[
  review_bot.parts.cache.base(
    params.prefix, params.cache),

  review_bot.parts.cron.base(
    params.prefix, "review",
    "*/3 * * * *", params.cpu, params.memory, params.image,
     "osrt-check_source --verbose --group factory-auto review"),
]
