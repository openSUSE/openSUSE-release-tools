How to validate the yaml
========================

For some reason, valid yaml is not supported - the indentation required
is rather wild. But to check the syntax before commit, you can use the
plugin locally

wget https://github.com/tomzo/gocd-yaml-config-plugin/releases/download/0.9.0/yaml-config-plugin-0.9.0.jar
#> java -jar yaml-config-plugin-0.9.0.jar syntax sp1-stagings.gocd.yaml
{"valid":true}
