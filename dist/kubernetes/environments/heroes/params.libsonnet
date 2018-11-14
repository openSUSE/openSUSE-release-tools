local params = std.extVar("__ksonnet/params");
local globals = import "globals.libsonnet";
local envParams = params + {
  components+: {
    "obs-operator.service"+: {
      externalIPs: [
        "192.168.47.44",
        "192.168.47.45",
        "192.168.47.46",
      ],
      externalPort: 31452,
    },
    "repo-checker.project_only"+: {
      projects: [
        "openSUSE:Factory",
        "openSUSE:Leap:15.0:Update",
        "openSUSE:Leap:15.1",
      ],
    },
  },
};

{
  components: {
    [x]: envParams.components[x] + globals,
    for x in std.objectFields(envParams.components)
  },
}
