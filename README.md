# Proyecto Final — Comportamiento de una aplicación bajo diferentes escenarios de despliegue

API REST de una **tienda online** (FastAPI + PostgreSQL) desplegada con Docker Compose y con Kubernetes (K3s)
en 1, 2 y 3 nodos de Azure, probada con JMeter y monitoreada con Prometheus + Grafana.

**Integrantes:** Thomas Perez, David Suarez, Jesus Santodomingo

**Repositorio:** https://github.com/Tapr16/proyecto-final · **Notebook:** [`notebook/analisis.ipynb`](notebook/analisis.ipynb)

## Estructura

```
app/                 API FastAPI, seed.py (datos), Dockerfile
docker-compose.yml   Fase 1 (Docker)
docker-compose.monitoring.yml + monitoring/   Prometheus, Grafana, cAdvisor, node-exporter (Fase 1)
k8s/                 Manifiestos de Kubernetes (00..06) y monitoring/ (Helm values + ServiceMonitor)
infra/               Scripts de Azure (crear/apagar/borrar VMs) y preparación de VMs
jmeter/              plan.jmx, ids.csv, run_scenario.sh
scripts/             exportar_prometheus.py (métricas de cada corrida a CSV)
results/             .jtl de cada escenario, runs.csv y results/prometheus/*.csv
notebook/            analisis.ipynb (Colab)
docs/                reporte.pdf y bitácora
```

## Requisitos

Docker, kubectl, K3s, Helm 3, JMeter 5.6.3, Python 3.12, Azure CLI (`az`).

---

## 0. Configuración única: imagen en GitHub Container Registry (GHCR)

La imagen de la API se publica en **GHCR**, el "Docker Hub" de GitHub, para que las VMs puedan descargarla:
`ghcr.io/tapr16/pf-api:1.0` (GHCR exige el usuario en minúsculas).

Publicar la imagen (una sola vez, siempre con el tag `1.0`), con la sesión de `gh` iniciada
(`gh auth login --web --scopes write:packages`):

1. Construir y subir:
   ```bash
   docker build -t ghcr.io/tapr16/pf-api:1.0 ./app
   gh auth token | docker login ghcr.io -u Tapr16 --password-stdin
   docker push ghcr.io/tapr16/pf-api:1.0
   ```
2. En GitHub → *Packages → pf-api → Package settings* → **Change visibility → Public**
   (si queda privada, Kubernetes da `ImagePullBackOff`).

> Si cambian el código de la API, vuelvan a construir y subir **antes** de empezar las mediciones.
> Durante el experimento la imagen no debe cambiar.

También hay que cambiar `CAMBIAR-CLAVE` en `k8s/01-postgres-secret.yaml` (2 veces) y en `k8s/monitoring/values.yaml`.

---

## 1. Probar en local (opcional, en su PC)

```bash
cp .env.example .env              # y editar las claves
docker compose up -d --build
docker compose run --rm api python seed.py --escala 0.05   # 5 % de los datos (rápido)
curl http://localhost:8000/health
```

Documentación interactiva de la API: http://localhost:8000/docs

Si el puerto 8000 ya está ocupado en su PC, agreguen `API_PORT=8088` al `.env` (en las VMs déjenlo en 8000).
Para apagar: `docker compose down` (agregar `-v` para borrar también los datos).

## 2. Crear la infraestructura en Azure

Antes de crear nada:
1. *Suscripciones → Azure for Students → Uso + cuotas*: "Total Regional vCPUs" ≥ 8 y cuota de la familia **DASv5**.
   Si no alcanza, pidan aumento o repartan las VMs entre cuentas del grupo.
2. *Cost Management → Budgets*: alerta de presupuesto (p. ej. $30).
3. Azure for Students limita las regiones. En nuestra suscripción las permitidas son brazilsouth, spaincentral,
   switzerlandnorth, francecentral y northcentralus, y **solo `northcentralus` ofrece `Standard_D2as_v5`**
   (el script ya la usa por defecto). Para revisar las de otra cuenta:
   `az policy assignment list --query "[].parameters.listOfAllowedLocations.value" -o tsv`
4. Registrar los servicios necesarios (una vez por suscripción, no cobra nada):
   `az provider register -n Microsoft.Compute && az provider register -n Microsoft.Network && az provider register -n Microsoft.Storage`
5. Si sale `RequestDisallowedByPolicy`, la región no está permitida: `LOC=<otra> ./azure-create.sh crear`.
   Antes, borrar el grupo a medio crear con `./azure-create.sh borrar`.

```bash
cd infra
./azure-create.sh crear        # 4 VMs Standard_D2as_v5 + red vnet-pf
./azure-create.sh ips          # IPs públicas y privadas
```

En cada VM (`scp infra/preparar-vm.sh azureuser@<IP>:` y luego por SSH):

| VM | Comando |
|---|---|
| nodo1 | `./preparar-vm.sh docker` |
| nodo2, nodo3 | `./preparar-vm.sh nodo` |
| vm-jmeter | `./preparar-vm.sh jmeter` |

En nodo1 y vm-jmeter: `git clone <repo> proyecto-final`.

**Costos:** al terminar cada sesión de trabajo `./azure-create.sh apagar` (apagar desde Ubuntu NO deja de cobrar).
Al retomar: `./azure-create.sh encender`.

## 3. Fase 1 — Docker Compose (nodo1)

```bash
cd proyecto-final
cp .env.example .env    # editar claves
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
docker compose run --rm api python seed.py      # 100K productos, ~600K items (unos minutos)
curl http://localhost:8000/health
```

Grafana (desde su PC): `ssh -L 3000:localhost:3000 -L 9090:localhost:9090 azureuser@<IP_PUBLICA_NODO1>`
→ http://localhost:3000 (admin / GRAFANA_PASSWORD). Importar dashboards **1860** y **14282**.

Pruebas (en vm-jmeter):
```bash
cd proyecto-final/jmeter
./run_scenario.sh docker <IP_PRIVADA_NODO1> 8000
```

Exportar métricas **antes de cerrar la fase** (desde su PC, con el túnel a 9090 abierto):
```bash
pip install pandas requests                     # una vez
scp azureuser@<IP_PUBLICA_JMETER>:~/proyecto-final/results/runs.csv results/
python scripts/exportar_prometheus.py --docker --escenarios docker
```

Cerrar la fase **antes** de instalar K3s:
```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml down -v
sudo systemctl disable --now docker docker.socket
```

## 4. Kubernetes con K3s

### 4.1 nodo1 (server)

```bash
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644 \
  --node-ip <IP_PRIVADA_NODO1> --disable traefik
echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> ~/.bashrc && source ~/.bashrc
kubectl label node nodo1 rol=db
sudo cat /var/lib/rancher/k3s/server/node-token     # guardar para nodo2 y nodo3
```

(Traefik se desactiva porque no se usa: se entra por NodePort y así no consume recursos.)

### 4.2 Monitorización (Helm) — se instala una vez y se deja en todas las fases K8s

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace -f k8s/monitoring/values.yaml
```

Acceso:
```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 &
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090 &
# En su PC:
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 azureuser@<IP_PUBLICA_NODO1>
```

Grafana (admin / la clave de `values.yaml`) ya trae dashboards de Kubernetes: *Kubernetes / Compute Resources / Namespace (Pods)*
con namespace `proyecto` y *Node Exporter / Nodes*.

Al terminar cada fase de K8s (1, 2 y 3 nodos), exportar sus métricas desde su PC con el túnel abierto:
`python scripts/exportar_prometheus.py --escenarios k8s-1n-1r k8s-1n-2r k8s-1n-3r` (con `runs.csv` actualizado).

### 4.3 Desplegar la aplicación

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/                      # también lanza el seed
kubectl apply -f k8s/monitoring/api-servicemonitor.yaml
kubectl -n proyecto rollout status statefulset/postgres
kubectl -n proyecto wait --for=condition=complete job/seed --timeout=15m
kubectl -n proyecto rollout status deployment/api
kubectl -n proyecto get pods -o wide
```

### 4.4 Ciclo por escenario (repetir para cada número de réplicas)

En nodo1:
```bash
# 1. Resetear la BD
kubectl -n proyecto delete job seed --ignore-not-found
kubectl apply -f k8s/06-seed-job.yaml
kubectl -n proyecto wait --for=condition=complete job/seed --timeout=15m
# 2. Ajustar réplicas (1, 2 o 3)
kubectl -n proyecto scale deployment api --replicas=2
kubectl -n proyecto rollout status deployment/api
kubectl -n proyecto get pods -o wide        # captura para el notebook
```

En vm-jmeter:
```bash
./run_scenario.sh k8s-1n-2r <IP_PRIVADA_NODO1> 30080
```

| Fase | Escenarios |
|---|---|
| 1 nodo | `k8s-1n-1r`, `k8s-1n-2r`, `k8s-1n-3r` |
| 2 nodos | `k8s-2n-1r`, `k8s-2n-2r`, `k8s-2n-3r` |
| 3 nodos | `k8s-3n-1r`, `k8s-3n-2r`, `k8s-3n-3r` |

### 4.5 Agregar nodo2 (y luego nodo3)

En el nodo nuevo:
```bash
curl -sfL https://get.k3s.io | K3S_URL=https://<IP_PRIVADA_NODO1>:6443 \
  K3S_TOKEN=<token> sh -s - --node-ip <IP_PRIVADA_DEL_NODO>
```
En nodo1:
```bash
kubectl get nodes                                     # todos Ready
kubectl -n proyecto rollout restart deployment/api    # redistribuir réplicas
kubectl -n proyecto get pods -o wide                  # anotar en qué nodo quedó cada réplica
```

## 5. Reglas de rigor

1. Resetear la BD antes de cada escenario.
2. Todos los pods `Running` y `Ready` antes de lanzar JMeter.
3. El notebook descarta los primeros 30 s (ramp-up) de cada corrida.
4. No usar las VMs para otra cosa durante las pruebas.
5. Revisar `results/<escenario>/u*_vmstat.txt`: si la CPU de vm-jmeter (100 − columna `id`) pasa de 80 %, el cuello es JMeter.
6. Anotar todo en [`docs/bitacora.md`](docs/bitacora.md).

Si con 200 usuarios el throughput sigue subiendo: `LEVELS="10 50 100 200 400" ./run_scenario.sh ...`

## 6. Recoger resultados y borrar todo

Desde su PC, **antes de borrar**:
```bash
scp -r azureuser@<IP_PUBLICA_JMETER>:~/proyecto-final/results/* ./results/
python scripts/exportar_prometheus.py              # con el túnel a Prometheus abierto
git add results && git commit -m "Resultados" && git push
cd infra && ./azure-create.sh borrar
```

## 7. Ver el notebook

- **Colab:** *Archivo → Abrir cuaderno → GitHub* y pegar la URL del repositorio; elegir `notebook/analisis.ipynb`.
  Cambiar `REPO_URL` en la primera celda de código y ejecutar todo.
- **Local:** `pip install pandas matplotlib seaborn jupyter && jupyter notebook notebook/analisis.ipynb`

## Datos de prueba de JMeter (`jmeter/ids.csv`)

Ya viene generado (5.000 filas con IDs válidos de productos y clientes, categorías y términos de búsqueda).
Como los datos son deterministas, los IDs siempre existen tras el seed. Para regenerarlo:
`cd app && python seed.py --solo-csv ../jmeter/ids.csv`.

`plan.jmx` se puede abrir en la interfaz gráfica de JMeter para revisarlo, pero las pruebas se corren siempre en modo CLI.

## Problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| Pod `Pending` | No caben los `requests` de CPU | `kubectl -n proyecto describe pod <pod>`; bajar `requests` o documentarlo como hallazgo |
| `ImagePullBackOff` | Imagen de GHCR privada o nombre mal escrito | Hacerla pública o crear un `imagePullSecret` |
| nodo2 no se une | IP pública o token incorrecto | Usar la IP **privada** de nodo1 y revisar el token |
| `too many connections` | Pool × réplicas > `max_connections` | Bajar `DB_POOL_SIZE` |
| Más réplicas y el throughput no sube | BD o JMeter saturados | CPU de PostgreSQL (Grafana) y `u*_vmstat.txt` de vm-jmeter |
| Repeticiones muy distintas | BD sin resetear, VMs ocupadas | Revisar las reglas de la sección 5 |
| `port is already allocated` en local | Puerto 8000 ocupado | `API_PORT=8088` en `.env` |
| El crédito de Azure baja rápido | VMs encendidas sin uso | `./azure-create.sh apagar` al terminar cada sesión |

## Decisiones de diseño

- Se prueban **1, 2 y 3 nodos** (el enunciado a veces habla solo de uno y dos; confirmado con el profesor que son 1, 2 y 3).

- Todas las VMs `Standard_D2as_v5` (no serie B: sus créditos de CPU harían los resultados no comparables).
- JMeter en una VM de la misma red, no en un portátil.
- PostgreSQL: una sola instancia fija en nodo1 en todas las fases; solo se escala la API.
- `limits` iguales en Docker y K8s (API 1 CPU / 512 MB, PostgreSQL 1 CPU / 2 GB). Los `requests` de CPU en K8s
  son menores (API 250m, PostgreSQL 500m) para que las 3 réplicas quepan en un nodo de 2 vCPU.
- 1 worker de Uvicorn por contenedor; pool de 10 + 5 conexiones por réplica (máx. 45 con 3 réplicas).
- Datos deterministas (semilla 42). La búsqueda `ILIKE` no tiene índice a propósito (endpoint costoso).
