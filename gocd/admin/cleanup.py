import requests
import sys
import time
import subprocess
import shutil
import glob
import os
import psycopg2


token = open("gocd_api.token").read().strip()
api_prefix = "http://localhost:8153/go/api"
container_image = "registry.opensuse.org/opensuse/tools/images/containers_tumbleweed/gocd-agent-release-tools"
# api_prefix = 'https://botmaster.suse.de/go/api'


def botmaster_headers(version):
    headers = {"Authorization": f"Bearer {token}"}
    headers["X-GoCD-Confirm"] = "true"
    headers["Accept"] = f"application/vnd.go.cd.v{version}+json"
    headers["Content-Type"] = "application/json"
    return headers


def botmaster_get(url, version):
    url = api_prefix + url
    return requests.get(url, headers=botmaster_headers(version))


def botmaster_post(url, version):
    url = api_prefix + url
    return requests.post(url, headers=botmaster_headers(version))


def botmaster_delete(url, version):
    url = api_prefix + url
    return requests.delete(url, headers=botmaster_headers(version))


def botmaster_patch(url, data, version):
    url = api_prefix + url
    return requests.patch(url, data, headers=botmaster_headers(version))


def delete_agents(only_disable=False):
    x = botmaster_get("/agents", version=7)
    if x.status_code not in [200]:
        print("Can't retrieve agent list")
        sys.exit(1)
    agents = x.json()["_embedded"]["agents"]
    for agent in agents:
        url = f'/agents/{agent["uuid"]}'
        # first needs to be disabled
        if agent["agent_config_state"] != "Disabled":
            x = botmaster_patch(url, '{"agent_config_state": "Disabled"}', version=7)
            if x.status_code != 200:
                print("Can't disable agent", url, x, x.content)
                continue
        if not only_disable:
            botmaster_delete(url, version=7)


def cleanup_cache():
    for file in glob.glob("/srv/go-repository-cache/repo-*solv*"):
        os.unlink(file)
    for suffix in ["openSUSE:Maintenance:*", "*:Staging:adi:*", "repo-*"]:
        for dir in glob.glob(f"/srv/go-repository-cache/{suffix}"):
            shutil.rmtree(dir)
    for root, dir, files in os.walk("/srv/go-repository-cache/"):
        if root.endswith("/.cache"):
            for file in files:
                os.unlink(os.path.join(root, file))


def remove_old_runs(cur, pipeline):
    cur.execute(
        """SELECT distinct p.id,p.label from pipelines p join stages s on s.pipelineid = p.id
                  and s.createdtime < current_date - interval '5' day and p.name=%s""",
        (pipeline,),
    )
    ids = []
    for row in cur.fetchall():
        id, label = row
        print("Remove", pipeline, label)
        path = f"/var/lib/go-server/artifacts/pipelines/{pipeline}/{label}"
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        ids.append(id)
    cur.execute(
        "delete from BUILDSTATETRANSITIONS where STAGEID in (select id from stages where pipelineid = ANY(%s))",
        (ids,),
    )
    cur.execute(
        "delete from PIPELINEMATERIALREVISIONS where pipelineid = ANY(%s)", (ids,)
    )
    cur.execute(
        "delete from builds where stageid in (select id from stages where pipelineid = ANY(%s))",
        (ids,),
    )
    cur.execute("delete from stages where pipelineid = ANY(%s)", (ids,))
    cur.execute("delete from pipelines where id = ANY(%s)", (ids,))
    cur.execute("update pipelines set NATURALORDER=0 where name = %s", (pipeline,))


def cleanup_old_pipelines():
    # very safe password - only reachable from localhost
    conn = psycopg2.connect(
        "dbname=gocd user=gocd_database_user password=gocd_database_password host=localhost"
    )
    with conn.cursor() as cur:
        cur.execute("SELECT distinct name from pipelines")
        pipelines = sorted([row[0] for row in cur.fetchall()])
        for pipeline in pipelines:
            remove_old_runs(cur, pipeline)
            conn.commit()
        cur.execute(
            """delete from modifiedfiles where modificationid in
               (select id from modifications where modifiedtime < current_date - interval '10' day)"""
        )
        # cur.execute("delete from modifications where modifiedtime < current_date - interval '10' day")
    conn.close()


def main():
    # Make a tag of the preivous image
    subprocess.run(
        [
            "docker",
            "tag",
            container_image + ":latest",
            container_image + ":previous",
        ],
        check=True,
    )

    # pull new image - if registry is down, better stop here
    subprocess.run(
        [
            "docker",
            "pull",
            container_image + ":latest",
        ],
        check=True,
    )

    # disable all agents
    delete_agents(only_disable=True)

    # putting the server into maintenance mode
    x = botmaster_post("/admin/maintenance_mode/enable", version=1)
    if x.status_code not in [204, 409]:
        print("Failed to enable maintenance mode", x, x.content)
        sys.exit(1)

    # wait for all jobs to finish - cancel the monitors manually
    while True:
        info = botmaster_get("/admin/maintenance_mode/info", version=1)
        if info.status_code not in [200]:
            print("Failed to retrieve maintenance mode info", info, info.content)
            sys.exit(1)
        info = info.json()
        if not info["is_maintenance_mode"]:
            print("Failed to enable maintenance mode", x, x.content)
            sys.exit(1)

        running_systems = info["attributes"]["running_systems"]["building_jobs"]
        if len(running_systems) == 0:
            break
        pipelines = []
        for job in running_systems:
            if job["pipeline_name"] in [
                "SUSE.Repo.Monitor",
                "SUSE.openQA",
                "openSUSE.Repo.Monitor",
                "openSUSE.openQA",
            ]:
                url = f"/stages/{job['pipeline_name']}/{job['pipeline_counter']}/{job['stage_name']}/{job['stage_counter']}/cancel"
                x = botmaster_post(url, version=3)
                if x.status_code not in [200]:
                    print(f"Can't cancel {job['pipline_name']}")
                    print(x, x.content)
                    sys.exit(1)
            else:
                pipelines.append(job["pipeline_name"])
        print("Waiting 2 min for the jobs to finish", sorted(pipelines))
        time.sleep(120)

    # stop all agents
    subprocess.run(["systemctl", "stop", "go-agent-*"], check=True)

    # cleanup
    proc = subprocess.Popen(["docker", "system", "prune"], stdin=subprocess.PIPE)
    proc.communicate(input=b"y\n")

    # stop the server
    subprocess.run(["systemctl", "stop", "go-server.service"])

    cleanup_old_pipelines()

    # start the server again
    subprocess.run(["systemctl", "start", "go-server.service"])

    while True:
        try:
            info = botmaster_get("/admin/maintenance_mode/info", version=1)
            print(info, info.content)
            if info.status_code in [503]:
                # is starting
                time.sleep(5)
                continue
            break
        except requests.exceptions.ConnectionError:
            time.sleep(5)
            continue

    delete_agents()
    cleanup_cache()

    # start the agents on new image
    subprocess.run(["systemctl", "start", "--all", "go-agent-*"], check=True)


main()
