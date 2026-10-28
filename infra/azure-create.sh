#!/usr/bin/env bash
# Gestión de las VMs del proyecto en Azure.
#
# Plan A (guía): 4 VMs Standard_D2as_v5 en una sola suscripción (requiere cuota DASv5 ≥ 8).
#   ./azure-create.sh crear
#
# Plan B (cuota de Azure for Students: 6 vCPU, DASv5 = 0, 3 IPs públicas):
#   - Cuenta 1 (clúster):  PLAN=B ./azure-create.sh crear          -> nodo1..3 B2as_v2
#   - Cuenta 2 (JMeter):   PLAN=B ./azure-create.sh crear-jmeter   -> vm-jmeter
#   - Cuenta 1:            ./azure-create.sh abrir-jmeter <IP_PUBLICA_JMETER>
#
# Otros comandos (cada cuenta maneja solo sus VMs):
#   ips | apagar | encender | creditos | borrar
set -euo pipefail

PLAN=${PLAN:-A}
LOC=${LOC:-northcentralus}       # Azure for Students: única región permitida con estos tamaños
RG=rg-pf
RG_JMETER=rg-pf-jmeter
if [ "$PLAN" = "B" ]; then
  SIZE=${SIZE:-Standard_B2as_v2}  # burstable: vigilar créditos de CPU (./azure-create.sh creditos)
  NODOS="nodo1 nodo2 nodo3"
else
  SIZE=${SIZE:-Standard_D2as_v5}  # no burstable: resultados comparables
  NODOS="nodo1 nodo2 nodo3 vm-jmeter"
fi

crear_vm() {   # crear_vm <grupo> <nombre> [args extra de az vm create]
  local G=$1 VM=$2; shift 2
  az vm create -g "$G" -n "$VM" \
    --image Ubuntu2404 --size "$SIZE" \
    --admin-username azureuser --generate-ssh-keys \
    --public-ip-sku Standard --os-disk-size-gb 64 "$@" -o none
}

# Grupos de esta suscripción que existen (rg-pf y/o rg-pf-jmeter)
grupos() {
  for G in $RG $RG_JMETER; do
    [ "$(az group exists -n $G)" = "true" ] && echo $G
  done
}

en_cada_vm() {   # en_cada_vm <comando az vm> [args]
  for G in $(grupos); do
    for VM in $(az vm list -g $G --query "[].name" -o tsv); do
      echo ">> $1 $VM"; az vm "$@" -g $G -n $VM --no-wait
    done
  done
}

case "${1:-}" in
  crear)
    az group create -n $RG -l $LOC -o none
    az network vnet create -g $RG -n vnet-pf \
      --address-prefix 10.0.0.0/16 --subnet-name snet-pf --subnet-prefix 10.0.1.0/24 -o none
    for VM in $NODOS; do
      echo ">> creando $VM ($SIZE)"
      crear_vm $RG $VM --vnet-name vnet-pf --subnet snet-pf
    done
    az vm list-ip-addresses -g $RG -o table
    ;;
  crear-jmeter)   # Plan B: correr en la suscripción de OTRO integrante
    az group create -n $RG_JMETER -l $LOC -o none
    echo ">> creando vm-jmeter ($SIZE)"
    crear_vm $RG_JMETER vm-jmeter
    az vm list-ip-addresses -g $RG_JMETER -o table
    ;;
  abrir-jmeter)   # Plan B: permitir SOLO la IP de vm-jmeter hacia la API de nodo1
    IP=${2:?Uso: $0 abrir-jmeter <IP_PUBLICA_JMETER>}
    az network nsg rule create -g $RG --nsg-name nodo1NSG -n permitir-jmeter \
      --priority 1010 --direction Inbound --access Allow --protocol Tcp \
      --source-address-prefixes "$IP" --destination-port-ranges 8000 30080 -o table
    ;;
  ips)
    for G in $(grupos); do az vm list-ip-addresses -g $G -o table; done
    ;;
  apagar)      # deallocate: deja de cobrar CPU (apagar desde Ubuntu NO basta)
    en_cada_vm deallocate
    ;;
  encender)
    en_cada_vm start
    ;;
  creditos)    # Plan B: créditos de CPU restantes (serie B). Si llegan a 0, la CPU se limita.
    for G in $(grupos); do
      for VM in $(az vm list -g $G --query "[].name" -o tsv); do
        ID=$(az vm show -g $G -n $VM --query id -o tsv)
        C=$(az monitor metrics list --resource "$ID" --metric "CPU Credits Remaining" \
              --interval PT5M --offset 1h \
              --query "value[0].timeseries[0].data[?average!=\`null\`] | [-1].average" -o tsv)
        echo "$VM: ${C:-sin datos (¿VM apagada o recién creada?)}"
      done
    done
    ;;
  borrar)      # SOLO al final, después de copiar results/
    for G in $(grupos); do az group delete -n $G --yes --no-wait; echo ">> borrando $G"; done
    ;;
  *)
    sed -n '2,14p' "$0"; exit 1 ;;
esac
