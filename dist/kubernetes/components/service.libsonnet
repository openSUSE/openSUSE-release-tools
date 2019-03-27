{
  parts:: {
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

    deployment:: {
      base(prefix, name, cpu, memory, image, command):: {
        apiVersion: "apps/v1",
        kind: "Deployment",
        metadata: {
          name: prefix + "-" + name,
          labels: {
            app: prefix,
          },
        },
        spec: {
          replicas: 1,
          selector: {
            matchLabels: {
              app: prefix,
            },
          },
          template: {
            metadata: {
              labels: {
                app: prefix,
              },
            },
            spec: {
              containers: [{
                name: "service",
                image: image,
                args: [
                  "/bin/bash", "-c",
                  "cp /secret/.oscrc /root && osc staging --version && " + command
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
                },
              ],
            }
          }
        }
      }
    },

    service:: {
      base(prefix, name, internalPort, externalIPs, externalPort):: {
        apiVersion: "v1",
        kind: "Service",
        metadata: {
          name: prefix + "-" + name,
        },
        spec: {
          type: "NodePort",
          selector: {
            app: prefix,
          },
          ports: [{
            protocol: "TCP",
            port: internalPort,
            nodePort: externalPort,
          }],
          externalIPs: externalIPs,
        }
      }
    },
  }
}
