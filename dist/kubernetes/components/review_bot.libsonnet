{
  parts:: {
    cron:: {
      base(prefix, name, schedule, cpu, memory, image, command):: {
        apiVersion: "batch/v1beta1",
        kind: "CronJob",
        metadata: {
          name: prefix + "-" + name,
        },
        spec: {
          schedule: schedule,
          concurrencyPolicy: "Forbid",
          jobTemplate: { spec: { template: { spec: {
            containers: [{
              name: "worker",
              image: image,
              args: [
                "/bin/bash", "-c",
                "cp /secret/.oscrc /root && osc staging --version && du -h ~/.cache && " + command
              ],
              volumeMounts: [
                {
                  name: "oscrc",
                  mountPath: "/secret",
                  readOnly: true,
                },
                {
                  name: "cache",
                  mountPath: "/root/.cache",
                },
              ],
              resources: {
                requests: {
                  cpu: cpu,
                  memory: memory,
                }
              }
            }],
            restartPolicy: "Never",
            volumes: [
              {
                name: "oscrc",
                secret: {
                  secretName: prefix + "-oscrc",
                }
              },
              {
                name: "cache",
                persistentVolumeClaim: {
                  claimName: prefix + "-pvc"
                }
              }
            ],
          } } } }
        }
      }
    },

    cache:: {
      base(prefix, capacity):: {
        apiVersion: "v1",
        kind: "PersistentVolumeClaim",
        metadata: {
          name: prefix + "-pvc",
        },
        spec: {
          accessModes: ["ReadWriteMany"],
          resources: {
            requests: {
              storage: capacity,
            }
          }
        }
      }
    },
  }
}
