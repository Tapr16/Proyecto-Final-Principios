"""Exporta métricas de Prometheus de cada corrida a CSV (results/prometheus/).

Correr desde tu PC con el túnel abierto hacia Prometheus, por ejemplo:
    # en nodo1:  kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090
    # en tu PC:  ssh -L 9090:localhost:9090 azureuser@<IP_PUBLICA_NODO1>
    python scripts/exportar_prometheus.py --escenarios k8s-1n-1r k8s-1n-2r

Hay que hacerlo ANTES de borrar el clúster: después ya no existirán los datos.
Para la Fase 1 (Docker) use --docker: las consultas usan las etiquetas de cAdvisor.
"""
import argparse
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parent.parent
RESULTS = RAIZ / "results"

CONSULTAS_K8S = {
    "cpu_pod": 'sum(rate(container_cpu_usage_seconds_total{namespace="proyecto",container!=""}[1m])) by (pod)',
    "mem_pod": 'sum(container_memory_working_set_bytes{namespace="proyecto",container!=""}) by (pod)',
    "throttling_pod": 'sum(rate(container_cpu_cfs_throttled_periods_total{namespace="proyecto",container!=""}[1m])) by (pod)',
    "cpu_nodo": '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) by (instance)',
    "rps_api": 'sum(rate(http_requests_total{namespace="proyecto"}[1m])) by (pod)',
}
CONSULTAS_DOCKER = {
    "cpu_pod": 'sum(rate(container_cpu_usage_seconds_total{name=~".+"}[1m])) by (name)',
    "mem_pod": 'sum(container_memory_working_set_bytes{name=~".+"}) by (name)',
    "throttling_pod": 'sum(rate(container_cpu_cfs_throttled_periods_total{name=~".+"}[1m])) by (name)',
    "cpu_nodo": '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) by (instance)',
    "rps_api": 'sum(rate(http_requests_total[1m])) by (instance)',
}


def exportar(prom, query, start, end, step="15s"):
    r = requests.get(f"{prom}/api/v1/query_range",
                     params={"query": query, "start": start, "end": end, "step": step},
                     timeout=60)
    r.raise_for_status()
    filas = []
    for serie in r.json()["data"]["result"]:
        m = serie["metric"]
        etiqueta = m.get("pod") or m.get("name") or m.get("instance") or "total"
        filas += [{"serie": etiqueta, "ts": float(t), "valor": float(v)}
                  for t, v in serie["values"]]
    return pd.DataFrame(filas, columns=["serie", "ts", "valor"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prom", default="http://localhost:9090")
    p.add_argument("--runs", default=str(RESULTS / "runs.csv"))
    p.add_argument("--escenarios", nargs="*", help="por defecto, todos los de runs.csv")
    p.add_argument("--docker", action="store_true", help="usar consultas de cAdvisor")
    args = p.parse_args()

    runs = pd.read_csv(args.runs)
    if args.escenarios:
        runs = runs[runs.escenario.isin(args.escenarios)]
    consultas = CONSULTAS_DOCKER if args.docker else CONSULTAS_K8S
    destino = RESULTS / "prometheus"
    destino.mkdir(parents=True, exist_ok=True)

    for run in runs.itertuples():
        for nombre, query in consultas.items():
            df = exportar(args.prom, query, run.inicio, run.fin)
            df.insert(0, "metrica", nombre)
            archivo = destino / f"{run.escenario}_u{run.usuarios}_r{run.rep}_{nombre}.csv"
            df.to_csv(archivo, index=False)
        print(f"ok {run.escenario} u{run.usuarios} r{run.rep}")


if __name__ == "__main__":
    main()
