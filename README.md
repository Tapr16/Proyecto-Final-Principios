# Proyecto Final — Comportamiento de una aplicación bajo diferentes escenarios de despliegue

API REST de una **tienda online** (FastAPI + PostgreSQL) desplegada con Docker Compose y con Kubernetes (K3s)
en 1, 2 y 3 nodos de Google Cloud, probada con JMeter y monitoreada con Prometheus + Grafana.

**Integrantes:** Thomas Perez, David Suarez, Jesus Santodomingo

**Repositorio:** https://github.com/Tapr16/Proyecto-Final-Principios · **Notebook:** [`notebook/analisis.ipynb`](notebook/analisis.ipynb)

## Estructura

```
app/                 API FastAPI, seed.py (datos), Dockerfile
docker-compose.yml   Fase 1 (Docker)
docker-compose.monitoring.yml + monitoring/   Prometheus, Grafana, cAdvisor, node-exporter (Fase 1)
k8s/                 Manifiestos de Kubernetes (00..06) y monitoring/ (Helm values + ServiceMonitor)
infra/               gcp-create.sh (Google Cloud), preparar-vm.sh y azure-create.sh (alternativa descartada)
jmeter/              plan.jmx, ids.csv, run_scenario.sh
scripts/             exportar_prometheus.py (métricas de cada corrida a CSV)
results/             .jtl de cada escenario, runs.csv y results/prometheus/*.csv
notebook/            analisis.ipynb (Colab)
docs/                reporte.pdf y bitácora
```

## Requisitos

Docker, kubectl, K3s, Helm 3, JMeter 5.6.3, Python 3.12, Google Cloud CLI (`gcloud`).

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

## 2. Crear la infraestructura en Google Cloud

Se usa el **crédito de la prueba gratuita** (1.001.736 COP ≈ USD 300, vence a los 90 días) en el proyecto
`pf-experimento-tapr16`, región `us-central1`, zona `us-central1-c` (en `us-central1-a` no había capacidad de
`n2d-standard-2` el día de la creación; `gcp-create.sh` detecta la zona de nodo1).
Elegimos Google Cloud porque Azure for Students no tenía cuota para el plan (ver [Anexo A](#anexo-a--azure-alternativa-descartada)).

| VM | Tipo | IP privada (fija) | Rol |
|---|---|---|---|
| nodo1 | `n2d-standard-2` (2 vCPU AMD, 8 GB) | `10.0.1.10` | Docker (Fase 1), K3s server, PostgreSQL, monitoreo |
| nodo2 | `n2d-standard-2` | `10.0.1.11` | K3s agent (2 nodos) |
| nodo3 | `n2d-standard-2` | `10.0.1.12` | K3s agent (3 nodos) |
| vm-jmeter | `n2d-standard-2` | `10.0.1.20` | JMeter |

Todas iguales y **no burstable** (sin créditos de CPU), en la red privada `vpc-pf` (`10.0.1.0/24`).
Entre las VMs se permite todo el tráfico; desde internet solo SSH. Grafana y Prometheus se ven por túnel SSH.

Antes de crear nada (una vez):
1. Instalar Google Cloud CLI (`winget install -e --id Google.CloudSDK`) e iniciar sesión: `gcloud auth login`.
2. Proyecto activo: `gcloud config set project pf-experimento-tapr16`.
3. Revisar cuotas: `./infra/gcp-create.sh cuotas`. Se necesitan 8 de `CPUS_ALL_REGIONS` y de `N2D_CPUS`,
   4 de `IN_USE_ADDRESSES` y 200 GB de `SSD_TOTAL_GB` (en nuestra cuenta: 12, 16, 8 y 250).
4. Protección contra cobros (ya configurada en el proyecto). La cuenta de facturación es **pagada**, así que si
   el crédito se acabara Google cobraría a la tarjeta. Para evitarlo:
   - **Presupuesto "PF experimento - gasto real"**: 5.000 COP/mes **descontando créditos** (solo cuenta lo que se
     pagaría con la tarjeta). Avisa por correo al 50 % y al 100 %.
   - **Apagado automático** (`infra/corte-facturacion/`, Cloud Function `corte-facturacion`): recibe los avisos
     del presupuesto por Pub/Sub (`presupuesto-pf`) y, si el gasto real supera los 5.000 COP, **desactiva la
     facturación del proyecto**, lo que detiene las VMs. Reacciona con unas horas de retraso (así reporta Google).
   - Prueba segura (no corta nada): `gcloud pubsub topics publish presupuesto-pf --message='{"simulacro": true}'`
     y ver el resultado con `gcloud functions logs read corte-facturacion --gen2 --region=us-central1`.
   - Si se disparó y hay que seguir: revisar qué generó el gasto y reactivar con
     `gcloud billing projects link pf-experimento-tapr16 --billing-account=018AEE-F2873D-264C97`.

```bash
cd infra
./gcp-create.sh crear      # red + firewall + 4 VMs (unos 3 minutos)
./gcp-create.sh ips        # IPs privadas y públicas
```

Preparar cada VM (desde su PC, en la carpeta del repo):

```bash
gcloud compute scp infra/preparar-vm.sh nodo1:~ --zone us-central1-c
./infra/gcp-create.sh ssh nodo1      # y dentro: ./preparar-vm.sh docker
```

| VM | Comando dentro de la VM |
|---|---|
| nodo1 | `./preparar-vm.sh docker` (también fija el almacenamiento `overlay2`, necesario para cAdvisor) |
| nodo2, nodo3 | `./preparar-vm.sh nodo` |
| vm-jmeter | `./preparar-vm.sh jmeter` |

En nodo1 y vm-jmeter: `git clone https://github.com/Tapr16/Proyecto-Final-Principios.git proyecto-final`.

La primera vez, `gcloud compute ssh` crea una llave SSH en su PC. Cada integrante que quiera entrar debe tener
acceso al proyecto (consola → *IAM* → agregar su correo con el rol *Compute Instance Admin* o *Editor*).

**Costos:** cada `n2d-standard-2` cuesta unos USD 0,08/hora (4 VMs × 60 h ≈ USD 20 del crédito).
Al terminar cada sesión de trabajo: `./infra/gcp-create.sh apagar` (detiene CPU y RAM; el disco sigue cobrando muy poco).
Al retomar: `./infra/gcp-create.sh encender`. Las IPs privadas no cambian; las públicas sí (no importa, `gcp-create.sh ssh` las resuelve).

## 3. Fase 1 — Docker Compose (nodo1)

```bash
cd proyecto-final
cp .env.example .env    # editar claves
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
docker compose run --rm api python seed.py      # 100K productos, ~600K items (unos minutos)
curl http://localhost:8000/health
```

Grafana (desde su PC): `./infra/gcp-create.sh ssh nodo1 -- -L 3000:localhost:3000 -L 9090:localhost:9090`
→ http://localhost:3000 (admin / GRAFANA_PASSWORD). Importar dashboards **1860** y **14282**.

Pruebas (en vm-jmeter):
```bash
cd proyecto-final/jmeter
./run_scenario.sh docker 10.0.1.10 8000
```

Exportar métricas **antes de cerrar la fase** (desde su PC, con el túnel a 9090 abierto):
```bash
pip install pandas requests                     # una vez
gcloud compute scp vm-jmeter:~/proyecto-final/results/runs.csv results/ --zone us-central1-c
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
  --node-ip 10.0.1.10 --disable traefik
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
./infra/gcp-create.sh ssh nodo1 -- -L 3000:localhost:3000 -L 9090:localhost:9090
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
./run_scenario.sh k8s-1n-2r 10.0.1.10 30080
```

| Fase | Escenarios |
|---|---|
| 1 nodo | `k8s-1n-1r`, `k8s-1n-2r`, `k8s-1n-3r` |
| 2 nodos | `k8s-2n-1r`, `k8s-2n-2r`, `k8s-2n-3r` |
| 3 nodos | `k8s-3n-1r`, `k8s-3n-2r`, `k8s-3n-3r` |

### 4.5 Agregar nodo2 (y luego nodo3)

En el nodo nuevo:
```bash
curl -sfL https://get.k3s.io | K3S_URL=https://10.0.1.10:6443 \
  K3S_TOKEN=<token> sh -s - --node-ip <IP_DEL_NODO>    # nodo2: 10.0.1.11, nodo3: 10.0.1.12
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
gcloud compute scp --recurse vm-jmeter:~/proyecto-final/results/* ./results/ --zone us-central1-c
python scripts/exportar_prometheus.py              # con el túnel a Prometheus abierto
git add results && git commit -m "Resultados" && git push
./infra/gcp-create.sh borrar                        # VMs, red y firewall
gcloud projects delete pf-experimento-tapr16         # al final de todo: borra también el apagado automático (el presupuesto queda en la cuenta, sin costo)
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
| cAdvisor no muestra `api` ni `db` (solo servicios del sistema) | Docker 29 usa el almacén de containerd, que cAdvisor no reconoce | `/etc/docker/daemon.json` con `{"features": {"containerd-snapshotter": false}}` y reiniciar Docker (ya lo hace `preparar-vm.sh docker`) |
| `port is already allocated` en local | Puerto 8000 ocupado | `API_PORT=8088` en `.env` |
| El crédito de Google Cloud baja rápido | VMs encendidas sin uso | `./infra/gcp-create.sh apagar` al terminar cada sesión |
| `QUOTA_EXCEEDED` al crear VMs | Cuota de CPUs, IPs o disco SSD | `./infra/gcp-create.sh cuotas` |
| `gcloud compute ssh` pide permisos | El integrante no tiene acceso al proyecto | Agregarlo en *IAM* de la consola |

## Decisiones de diseño

- Se prueban **1, 2 y 3 nodos** (el enunciado a veces habla solo de uno y dos; confirmado con el profesor que son 1, 2 y 3).

- Todas las VMs `n2d-standard-2` en Google Cloud (equivalente a `Standard_D2as_v5` de la guía): no burstable,
  porque los créditos de CPU harían los resultados no comparables.
- JMeter en una VM de la misma red privada, no en un portátil.
- PostgreSQL: una sola instancia fija en nodo1 en todas las fases; solo se escala la API.
- `limits` iguales en Docker y K8s (API 1 CPU / 512 MB, PostgreSQL 1 CPU / 2 GB). Los `requests` de CPU en K8s
  son menores (API 250m, PostgreSQL 500m) para que las 3 réplicas quepan en un nodo de 2 vCPU.
- 1 worker de Uvicorn por contenedor; pool de 10 + 5 conexiones por réplica (máx. 45 con 3 réplicas).
- Datos deterministas (semilla 42). La búsqueda `ILIKE` no tiene índice a propósito (endpoint costoso).

## Anexo A — Azure (alternativa descartada)

Se intentó primero con Azure for Students (cuenta de Thomas, vía GitHub Student Pack). La cuota era de 6 vCPUs, 0 para la familia DASv5 y 3 IPs públicas, y Azure **rechazó el aumento**. Se deja `infra/azure-create.sh` (planes A y B) por si sirve en el futuro.


Antes de crear nada:
1. Revisar la cuota en `northcentralus`: `az vm list-usage -l northcentralus -o table`.
   El plan A necesita "Total Regional vCPUs" ≥ 8, "Standard DASv5 Family vCPUs" ≥ 8 y 4 IPs públicas.
   La cuota inicial de Azure for Students es **6 vCPUs, DASv5 = 0 y 3 IPs**: hay que pedir aumento
   (portal → *Cuotas*) o usar el **plan B** (sección A.1).
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

### A.1 Plan B — si Azure no aprueba el aumento de cuota

Con la cuota inicial de Azure for Students (6 vCPUs, solo serie B disponible, 3 IPs públicas):

| VM | Cuenta | Tamaño |
|---|---|---|
| nodo1, nodo2, nodo3 | Thomas (`rg-pf`) | `Standard_B2as_v2` (2 vCPU, 8 GB) |
| vm-jmeter | otro integrante (`rg-pf-jmeter`), misma región | `Standard_B2as_v2` |

```bash
# Cuenta de Thomas (az login con su cuenta)
PLAN=B ./azure-create.sh crear
# Cuenta de David o Jesus (az login con su cuenta; antes registrar los servicios, punto 4)
PLAN=B ./azure-create.sh crear-jmeter
# De vuelta en la cuenta de Thomas: abrir 8000 y 30080 de nodo1 SOLO para la IP de vm-jmeter
./azure-create.sh abrir-jmeter <IP_PUBLICA_JMETER>
```

Diferencias con el plan A:
- **JMeter ataca la IP pública de nodo1** (no la privada), porque está en otra suscripción:
  `./run_scenario.sh docker <IP_PUBLICA_NODO1> 8000`. Ambas VMs están en la misma región, así que la latencia
  sigue siendo baja; hay que mencionarlo en la metodología.
- **Créditos de CPU (serie B):** las VMs B acumulan créditos cuando están ociosas y los gastan bajo carga;
  si se agotan, Azure limita la CPU y los resultados dejan de ser comparables. Antes de **cada escenario**:
  ```bash
  ./azure-create.sh creditos      # en cada cuenta; esperar a que no estén cerca de 0
  ```
  Si un escenario agota los créditos (la métrica llega a 0 durante la corrida), repetirlo tras dejar las VMs
  encendidas sin carga. Registrar los créditos en la bitácora y reportarlo como limitación del experimento.
- Cada cuenta hace `apagar` / `encender` / `borrar` de sus propias VMs.
