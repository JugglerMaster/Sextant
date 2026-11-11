from __future__ import annotations

from tests.helpers import enroll_agent, heartbeat, headers, sample_metrics


def test_agents_csv_export(client, admin):
    enroll_agent(admin, hostname="csv-box")
    response = client.get("/api/export/agents.csv", headers=headers(admin))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "id,name,hostname" in response.text
    assert "csv-box" in response.text


def test_packages_csv_export(client, admin):
    agent = enroll_agent(admin)
    heartbeat(
        admin,
        agent,
        sample_metrics(
            packages=[{"manager": "apt", "package": "nginx", "version": "1.24", "arch": "amd64"}],
            package_managers=[{"name": "apt", "version": "2.7"}],
            package_inventory_hash="csvhash",
            package_inventory_full=True,
        ),
    )
    response = client.get(f"/api/export/packages.csv?agent_id={agent['agent_id']}", headers=headers(admin))
    assert response.status_code == 200
    assert "agent_id,agent_name,manager,package" in response.text
    assert "nginx" in response.text
    assert "1.24" in response.text
