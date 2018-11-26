local params = std.extVar("__ksonnet/params").components.service;
local service = import '../service.libsonnet';

[
  service.parts.deployment.base(
    params.prefix, "deployment",
    params.cpu, params.memory, params.image,
     "osrt-obs_operator --debug"),

  service.parts.service.base(
    params.prefix, "service", 8080, params.externalIPs, params.externalPort,
  ),
]
