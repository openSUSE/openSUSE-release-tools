# OSRT Kubernetes

The commands assume running from `dist/kubernetes`.

## namespace

If desired create the `osrt` namespace and set as current context namespace.

    kubectl create namespace osrt
    kubectl config set-context $(kubectl config current-context) --namespace=osrt

## secrets

Create secrets for each module, containing `.oscrc`, by enter OBS credentials.

    ./k8s-secret.py check-source
    ./k8s-secret.py repo-checker

## modules

Adjust modules in `app.yaml` or configure a new environment.

    ks env add --context heroes newenv
    ks env targets --module / --module check-source newenv

## configure

- See `environments/*/{globals,params}.libsonnet` for environment specific configuration of components.
- See `components/*` for more detailed changes (like the command executed).

## apply

    ks show heroes
    ks diff heroes
    ks apply heroes
