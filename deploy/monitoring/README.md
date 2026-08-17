# Feature 018 monitoring assets

These files define content-free health/readiness probes, alerts and a Grafana
dashboard. They never collect questions, answers, identity, cookies, request
bodies, file names or legal content.

The release compose file intentionally does not start Prometheus, Grafana or a
blackbox exporter. Those are additional background daemons and require the
operator's deployment approval. When an approved monitoring stack exists,
mount `prometheus.yml`, `blackbox.yml`, `feature018-alerts.yml` and import the
dashboard JSON. Keep the monitoring network private; do not publish exporter or
Prometheus ports through Caddy.
