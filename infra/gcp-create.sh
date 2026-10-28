#!/usr/bin/env bash
# Gestión de las VMs del proyecto en Google Cloud (prueba gratuita de 90 días).
#
#   ./gcp-create.sh crear       # red privada + firewall + 4 VMs n2d-standard-2
#   ./gcp-create.sh ips         # IPs privadas (fijas) y públicas (cambian al encender)
#   ./gcp-create.sh ssh nodo1   # entrar por SSH (gcloud maneja las llaves)
#   ./gcp-create.sh apagar      # detener las VMs: deja de cobrar CPU/RAM (el disco sigue cobrando poco)
#   ./gcp-create.sh encender
#   ./gcp-create.sh cuotas      # cuotas de CPU e IPs de la región
#   ./gcp-create.sh borrar      # SOLO al final, después de copiar results/
#
# Variables opcionales: PROYECTO (por defecto el de `gcloud config`), REGION, ZONA, TIPO.
set -euo pipefail

PROYECTO=${PROYECTO:-$(gcloud config get-value project 2>/dev/null)}
REGION=${REGION:-us-central1}
ZONA=${ZONA:-us-central1-a}
TIPO=${TIPO:-n2d-standard-2}   # 2 vCPU AMD EPYC, 8 GB, sin créditos de CPU (equivale a D2as_v5)
RED=vpc-pf
SUBRED=snet-pf
RANGO=10.0.1.0/24
VMS="nodo1 nodo2 nodo3 vm-jmeter"

[ -n "$PROYECTO" ] || { echo "Defina el proyecto: gcloud config set project <ID>"; exit 1; }
GC=(--project="$PROYECTO" --quiet)

ip_privada() {   # IPs privadas fijas: no cambian al apagar/encender
  case $1 in
    nodo1) echo 10.0.1.10 ;; nodo2) echo 10.0.1.11 ;;
    nodo3) echo 10.0.1.12 ;; vm-jmeter) echo 10.0.1.20 ;;
  esac
}

case "${1:-}" in
  crear)
    gcloud services enable compute.googleapis.com "${GC[@]}"
    gcloud compute networks create $RED --subnet-mode=custom "${GC[@]}"
    gcloud compute networks subnets create $SUBRED --network=$RED \
      --region="$REGION" --range=$RANGO "${GC[@]}"
    # Tráfico libre entre las VMs (K3s, PostgreSQL, JMeter -> API) y solo SSH desde afuera
    gcloud compute firewall-rules create pf-interno --network=$RED \
      --allow=tcp,udp,icmp --source-ranges=$RANGO "${GC[@]}"
    gcloud compute firewall-rules create pf-ssh --network=$RED \
      --allow=tcp:22 --source-ranges=0.0.0.0/0 "${GC[@]}"
    # Disco SSD pd-balanced de 50 GB: 4 × 50 GB cabe en la cuota SSD_TOTAL_GB (250 GB)
    for VM in $VMS; do
      echo ">> creando $VM ($TIPO, $(ip_privada $VM))"
      gcloud compute instances create "$VM" --zone="$ZONA" \
        --machine-type="$TIPO" \
        --subnet=$SUBRED --private-network-ip="$(ip_privada $VM)" \
        --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
        --boot-disk-size=50GB --boot-disk-type=pd-balanced "${GC[@]}"
    done
    "$0" ips
    ;;
  ips)
    gcloud compute instances list --project="$PROYECTO" \
      --format="table(name, networkInterfaces[0].networkIP:label=IP_PRIVADA, networkInterfaces[0].accessConfigs[0].natIP:label=IP_PUBLICA, status)"
    ;;
  ssh)
    VM=${2:?Uso: $0 ssh <vm> [-- argumentos de ssh]}; shift 2
    gcloud compute ssh "$VM" --zone="$ZONA" --project="$PROYECTO" "$@"
    ;;
  apagar)
    gcloud compute instances stop $VMS --zone="$ZONA" --async "${GC[@]}"
    ;;
  encender)
    gcloud compute instances start $VMS --zone="$ZONA" --async "${GC[@]}"
    ;;
  cuotas)
    echo "== Proyecto ($PROYECTO)    métrica / usado / límite"
    gcloud compute project-info describe --project="$PROYECTO" --flatten=quotas \
      --format="value(quotas.metric, quotas.usage, quotas.limit)" | grep -E "^CPUS_ALL_REGIONS\s"
    echo "== Región $REGION    métrica / usado / límite"
    gcloud compute regions describe "$REGION" --project="$PROYECTO" --flatten=quotas \
      --format="value(quotas.metric, quotas.usage, quotas.limit)" \
      | grep -E "^(CPUS|N2D_CPUS|IN_USE_ADDRESSES|SSD_TOTAL_GB|DISKS_TOTAL_GB)\s"
    ;;
  borrar)
    gcloud compute instances delete $VMS --zone="$ZONA" "${GC[@]}" || true
    gcloud compute firewall-rules delete pf-interno pf-ssh "${GC[@]}" || true
    gcloud compute networks subnets delete $SUBRED --region="$REGION" "${GC[@]}" || true
    gcloud compute networks delete $RED "${GC[@]}" || true
    ;;
  *)
    sed -n '2,12p' "$0"; exit 1 ;;
esac
